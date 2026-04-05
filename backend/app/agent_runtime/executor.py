from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.types import Checkpointer

from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.middleware_registry import (
    build_middleware_instances,
    get_provider_middleware,
)
from app.agent_runtime.model_factory import create_chat_model
from app.agent_runtime.streaming import stream_agent_response
from app.agent_runtime.tool_factory import (
    create_builtin_tool,
    create_prebuilt_tool,
    create_tool_from_db,
)

logger = logging.getLogger(__name__)


def _auth_config_to_headers(auth_config: dict | None) -> dict[str, str]:
    """auth_config를 HTTP 헤더로 변환."""
    if not auth_config:
        return {}
    if "headers" in auth_config:
        return auth_config["headers"]
    token = auth_config.get("api_key") or auth_config.get("jwt_token")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _url_to_server_key(url: str) -> str:
    """MCP 서버 URL을 고유 키로 변환 (호스트 + 경로 포함)."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    key = parsed.netloc + parsed.path
    return key.replace(".", "_").replace(":", "_").replace("/", "_").strip("_")


@asynccontextmanager
async def _mcp_context(
    mcp_servers: dict, langchain_tools: list, allowed_names: set[str]
):
    """MCP 서버가 있으면 연결을 열고 허용된 도구만 로딩, 없으면 no-op."""
    if mcp_servers:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        async with MultiServerMCPClient(mcp_servers, tool_name_prefix=True) as client:
            all_tools = await client.get_tools()
            langchain_tools.extend(
                t for t in all_tools if t.name in allowed_names
            )
            yield
    else:
        yield


def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    middleware: list | None = None,
    checkpointer: Checkpointer | None = None,
) -> Any:
    """Build an agent using create_deep_agent."""
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware or [],
        checkpointer=checkpointer,
    )


async def execute_agent_stream(
    provider: str,
    model_name: str,
    api_key: str | None,
    base_url: str | None,
    system_prompt: str,
    tools_config: list[dict[str, Any]],
    messages_history: list[dict[str, str]],
    thread_id: str,
    model_params: dict[str, Any] | None = None,
    middleware_configs: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[str, None]:
    model = create_chat_model(provider, model_name, api_key, base_url, **(model_params or {}))

    langchain_tools: list[BaseTool] = []
    mcp_servers: dict[str, dict] = {}
    mcp_allowed_names: set[str] = set()

    for tc in tools_config:
        tool_type = tc.get("type")
        if tool_type == "builtin":
            langchain_tools.append(create_builtin_tool(tc["name"]))
        elif tool_type == "prebuilt":
            langchain_tools.append(
                create_prebuilt_tool(tc["name"], auth_config=tc.get("auth_config"))
            )
        elif tool_type == "custom" and tc.get("api_url"):
            langchain_tools.append(
                create_tool_from_db(
                    name=tc["name"],
                    description=tc.get("description"),
                    api_url=tc["api_url"],
                    http_method=tc.get("http_method", "GET"),
                    parameters_schema=tc.get("parameters_schema"),
                    auth_type=tc.get("auth_type"),
                    auth_config=tc.get("auth_config"),
                )
            )
        elif tool_type == "mcp" and tc.get("mcp_server_url"):
            server_key = _url_to_server_key(tc["mcp_server_url"])
            mcp_servers[server_key] = {
                "transport": "streamable_http",
                "url": tc["mcp_server_url"],
                "headers": _auth_config_to_headers(tc.get("auth_config")),
            }
            # tool_name_prefix 적용 후 이름: "{server_key}_{tool_name}"
            mcp_tool_name = tc.get("mcp_tool_name", tc["name"])
            mcp_allowed_names.add(f"{server_key}_{mcp_tool_name}")
        elif tool_type == "skill_package":
            from app.agent_runtime.skill_tool_factory import create_skill_tools

            langchain_tools.extend(
                create_skill_tools(
                    skill_id=tc["skill_id"],
                    skill_dir=tc["skill_dir"],
                    conversation_id=tc.get("conversation_id"),
                    output_dir=tc.get("output_dir"),
                )
            )

    # Build middleware instances from agent config + provider auto-additions
    middleware = build_middleware_instances(middleware_configs or [])
    middleware += get_provider_middleware(provider)

    async with _mcp_context(mcp_servers, langchain_tools, mcp_allowed_names):
        agent = build_agent(
            model, langchain_tools, system_prompt, middleware=middleware or None
        )
        lc_messages = convert_to_langchain_messages(messages_history)
        config = {"configurable": {"thread_id": thread_id}}
        async for chunk in stream_agent_response(agent, lc_messages, config):
            yield chunk
