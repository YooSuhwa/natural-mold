"""Typed request/response boundary for the MCP Apps RemoteHost bridge."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class McpAppsReadParams(_StrictModel):
    uri: str = Field(min_length=1, max_length=1_000)
    server_id: str | None = Field(default=None, alias="serverId", max_length=64)


class McpAppsListParams(_StrictModel):
    server_id: str | None = Field(default=None, alias="serverId", max_length=64)


class McpAppsCallParams(_StrictModel):
    name: str = Field(min_length=1, max_length=150)
    arguments: dict[str, Any] = Field(default_factory=dict)
    server_id: str | None = Field(default=None, alias="serverId", max_length=64)


class McpAppsReadResourceCommand(_StrictModel):
    method: Literal["mcp-apps/read-resource", "resources/read"]
    params: McpAppsReadParams


class McpAppsListResourcesCommand(_StrictModel):
    method: Literal["resources/list"]
    params: McpAppsListParams = Field(default_factory=McpAppsListParams)


class McpAppsCallToolCommand(_StrictModel):
    method: Literal["tools/call"]
    params: McpAppsCallParams


McpAppsCommand = Annotated[
    McpAppsReadResourceCommand | McpAppsListResourcesCommand | McpAppsCallToolCommand,
    Field(discriminator="method"),
]


class McpAppArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    binding_id: uuid.UUID
    run_id: uuid.UUID
    resource_uri: str
    tool_meta: dict[str, Any]
    result_meta: dict[str, Any] | None = None


__all__ = ["McpAppArtifact", "McpAppsCommand"]
