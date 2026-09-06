"""Isolated authenticated fixture setup for the MCP Apps browser E2E."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.mcp_tool_loader import _build_mcp_tools
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db, owned_conversation, verify_csrf
from app.mcp.client import connect_and_list
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.services import conversation_run_service

router = APIRouter(tags=["e2e"])


class E2EMcpAppsFixtureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_url: str = Field(min_length=1, max_length=500)


class E2EMcpAppsFixtureResponse(BaseModel):
    agent_id: uuid.UUID
    conversation_id: uuid.UUID
    run_id: uuid.UUID
    mcp_server_id: uuid.UUID
    tool_call_id: str
    artifact: dict[str, Any]


def _require_loopback_fixture(url: str) -> None:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="E2E MCP fixture URL must be loopback /mcp"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise HTTPException(status_code=422, detail="E2E MCP fixture URL must be loopback /mcp")


@router.post(
    "/api/e2e/conversations/{conversation_id}/mcp-apps/fixture",
    response_model=E2EMcpAppsFixtureResponse,
)
async def create_e2e_mcp_apps_fixture(
    conversation_id: uuid.UUID,
    data: E2EMcpAppsFixtureCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    conversation: Conversation = Depends(owned_conversation),
) -> E2EMcpAppsFixtureResponse:
    """Create a real MCP ToolNode checkpoint and its trusted invocation binding."""

    if user.email != settings.e2e_user_email:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _require_loopback_fixture(data.server_url)
    discovery = await connect_and_list(transport="streamable_http", url=data.server_url)
    if not discovery.get("success"):
        raise HTTPException(status_code=502, detail="E2E MCP fixture discovery failed")
    descriptors = {
        item.get("name"): item
        for item in discovery.get("tools", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if "weather" not in descriptors or "refresh_weather" not in descriptors:
        raise HTTPException(status_code=422, detail="E2E MCP fixture tools are missing")

    server = McpServer(
        user_id=user.id,
        name=f"MCP Apps E2E {uuid.uuid4().hex[:8]}",
        transport="streamable_http",
        url=data.server_url,
        status="connected",
    )
    db.add(server)
    await db.flush()
    tools: dict[str, McpTool] = {}
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
        tools[name] = tool
    await db.flush()
    db.add_all(
        [
            AgentMcpToolLink(agent_id=conversation.agent_id, mcp_tool_id=tool.id)
            for tool in tools.values()
        ]
    )
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=user.id,
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

    runtime_tools = await _build_mcp_tools(
        [
            {
                "definition_key": "mcp",
                "name": tool.name,
                "mcp_server_url": data.server_url,
                "mcp_server_id": str(server.id),
                "mcp_tool_id": str(tool.id),
                "mcp_tool_name": tool.name,
                "mcp_transport_headers": {},
                "conversation_id": str(conversation.id),
                "user_id": str(user.id),
                "agent_id": str(conversation.agent_id),
                "credential_subject_user_id": str(user.id),
            }
            for tool in tools.values()
        ]
    )
    origin = next((tool for tool in runtime_tools if tool.name == "weather"), None)
    if origin is None:
        raise HTTPException(status_code=502, detail="E2E MCP fixture runtime tool failed")

    tool_call_id = f"e2e-mcp-app-{uuid.uuid4()}"
    graph_builder = StateGraph(MessagesState)
    graph_builder.add_node("tools", ToolNode([origin]))
    graph_builder.add_edge(START, "tools")
    graph_builder.add_edge("tools", END)
    graph = graph_builder.compile(checkpointer=get_checkpointer())
    state = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="Show the weather app."),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "weather",
                            "args": {"city": "Seoul"},
                            "id": tool_call_id,
                            "type": "tool_call",
                        }
                    ],
                ),
            ]
        },
        config={
            "configurable": {
                "thread_id": str(conversation.id),
                "moldy_run_id": str(run.id),
            }
        },
    )
    message = next(
        (
            item
            for item in reversed(state.get("messages", []))
            if isinstance(item, ToolMessage) and item.tool_call_id == tool_call_id
        ),
        None,
    )
    if message is None or not isinstance(message.artifact, dict):
        raise HTTPException(status_code=502, detail="E2E MCP fixture binding failed")

    await db.refresh(run)
    await conversation_run_service.transition_run(
        db,
        run,
        "completed",
        worker_instance_id="e2e-mcp-apps-helper",
    )
    await db.commit()
    return E2EMcpAppsFixtureResponse(
        agent_id=conversation.agent_id,
        conversation_id=conversation.id,
        run_id=run.id,
        mcp_server_id=server.id,
        tool_call_id=tool_call_id,
        artifact=message.artifact,
    )


__all__ = ["router"]
