from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

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


def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    middleware: list | None = None,
) -> Any:
    """Build an agent, preferring create_agent with middleware support.

    Falls back to create_react_agent if langchain.agents.create_agent
    is not available (e.g. langchain 1.x not yet installed).
    """
    if middleware:
        try:
            from langchain.agents import create_agent

            return create_agent(
                model=model,
                tools=tools,
                system_prompt=system_prompt,
                middleware=middleware,
            )
        except ImportError:
            logger.warning(
                "langchain.agents.create_agent not available; "
                "falling back to create_react_agent (middleware ignored)"
            )
    return create_react_agent(
        model=model,
        tools=tools,
        prompt=system_prompt,
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
    for tc in tools_config:
        if tc.get("type") == "builtin":
            tool = create_builtin_tool(tc["name"])
            langchain_tools.append(tool)
        elif tc.get("type") == "prebuilt":
            tool = create_prebuilt_tool(tc["name"], auth_config=tc.get("auth_config"))
            langchain_tools.append(tool)
        elif tc.get("type") == "custom" and tc.get("api_url"):
            tool = create_tool_from_db(
                name=tc["name"],
                description=tc.get("description"),
                api_url=tc["api_url"],
                http_method=tc.get("http_method", "GET"),
                parameters_schema=tc.get("parameters_schema"),
                auth_type=tc.get("auth_type"),
                auth_config=tc.get("auth_config"),
            )
            langchain_tools.append(tool)
        elif tc.get("type") == "mcp" and tc.get("mcp_server_url"):
            from app.agent_runtime.tool_factory import create_mcp_tool

            tool = create_mcp_tool(
                name=tc["name"],
                description=tc.get("description"),
                mcp_server_url=tc["mcp_server_url"],
                mcp_tool_name=tc.get("mcp_tool_name", tc["name"]),
                auth_config=tc.get("auth_config"),
            )
            langchain_tools.append(tool)
        elif tc.get("type") == "skill_package":
            from app.agent_runtime.skill_tool_factory import create_skill_tools

            skill_tools = create_skill_tools(
                skill_id=tc["skill_id"],
                skill_dir=tc["skill_dir"],
                conversation_id=tc.get("conversation_id"),
                output_dir=tc.get("output_dir"),
            )
            langchain_tools.extend(skill_tools)

    # Build middleware instances from agent config + provider auto-additions
    middleware = build_middleware_instances(middleware_configs or [])
    middleware += get_provider_middleware(provider)

    agent = build_agent(model, langchain_tools, system_prompt, middleware=middleware or None)
    lc_messages = convert_to_langchain_messages(messages_history)
    config = {"configurable": {"thread_id": thread_id}}

    async for chunk in stream_agent_response(agent, lc_messages, config):
        yield chunk
