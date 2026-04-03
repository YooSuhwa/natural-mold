from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.model_factory import create_chat_model
from app.agent_runtime.streaming import stream_agent_response
from app.agent_runtime.tool_factory import (
    create_builtin_tool,
    create_prebuilt_tool,
    create_tool_from_db,
)


def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
) -> Any:
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
) -> AsyncGenerator[str, None]:
    model = create_chat_model(
        provider, model_name, api_key, base_url, **(model_params or {})
    )

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

    agent = build_agent(model, langchain_tools, system_prompt)
    lc_messages = convert_to_langchain_messages(messages_history)
    config = {"configurable": {"thread_id": thread_id}}

    async for chunk in stream_agent_response(agent, lc_messages, config):
        yield chunk
