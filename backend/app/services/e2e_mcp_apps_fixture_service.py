"""Persistence boundary for the authenticated MCP Apps browser fixture."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run import ConversationRun
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.services import conversation_run_service


@dataclass(frozen=True, slots=True)
class E2EMcpAppToolRecord:
    id: uuid.UUID
    name: str


@dataclass(frozen=True, slots=True)
class E2EMcpAppsFixtureRecords:
    server_id: uuid.UUID
    run_id: uuid.UUID
    tools: tuple[E2EMcpAppToolRecord, ...]


async def create_fixture_records(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    server_url: str,
    descriptors: Mapping[str, Mapping[str, Any]],
) -> E2EMcpAppsFixtureRecords:
    """Create the disposable MCP server, linked tools, and owning run."""

    server = McpServer(
        user_id=user_id,
        name=f"MCP Apps E2E {uuid.uuid4().hex[:8]}",
        transport="streamable_http",
        url=server_url,
        status="connected",
    )
    db.add(server)
    await db.flush()

    tools: list[McpTool] = []
    for name in ("weather", "refresh_weather"):
        descriptor = descriptors[name]
        tool = McpTool(
            server_id=server.id,
            name=name,
            description=descriptor.get("description"),
            input_schema=descriptor.get("input_schema") or {},
            metadata_json=descriptor.get("metadata"),
            enabled=True,
        )
        db.add(tool)
        tools.append(tool)
    await db.flush()
    db.add_all([AgentMcpToolLink(agent_id=agent_id, mcp_tool_id=tool.id) for tool in tools])
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=agent_id,
        user_id=user_id,
        source="chat",
        input_preview="Show the weather app.",
    )
    await conversation_run_service.transition_run(
        db,
        run,
        "running",
        worker_instance_id="e2e-mcp-apps-helper",
    )
    await db.commit()
    return E2EMcpAppsFixtureRecords(
        server_id=server.id,
        run_id=run.id,
        tools=tuple(E2EMcpAppToolRecord(id=tool.id, name=tool.name) for tool in tools),
    )


async def complete_fixture_run(db: AsyncSession, run_id: uuid.UUID) -> None:
    run = await db.get(ConversationRun, run_id)
    if run is None:
        raise RuntimeError("E2E MCP Apps fixture run disappeared")
    await conversation_run_service.transition_run(
        db,
        run,
        "completed",
        worker_instance_id="e2e-mcp-apps-helper",
    )
    await db.commit()


__all__ = [
    "E2EMcpAppToolRecord",
    "E2EMcpAppsFixtureRecords",
    "complete_fixture_run",
    "create_fixture_records",
]
