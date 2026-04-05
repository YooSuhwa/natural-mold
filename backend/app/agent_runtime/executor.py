from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

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
    *,
    middleware: list | None = None,
    checkpointer: Any | None = None,
    store: Any | None = None,
    backend: Any | None = None,
    name: str | None = None,
) -> Any:
    """Build a deep agent. Returns CompiledStateGraph."""
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware or (),
        checkpointer=checkpointer,
        store=store,
        backend=backend,
        name=name,
    )


# ---------------------------------------------------------------------------
# MCP tool helpers — langchain-mcp-adapters
# ---------------------------------------------------------------------------


def _auth_config_to_headers(auth_config: dict | None) -> dict[str, str]:
    """auth_config를 HTTP 헤더로 변환."""
    if not auth_config:
        return {}
    if "headers" in auth_config:
        return auth_config["headers"]
    token = auth_config.get("jwt_token") or auth_config.get("api_key")
    if token:
        header_name = auth_config.get("header_name", "Authorization")
        if auth_config.get("jwt_token"):
            return {"Authorization": f"Bearer {token}"}
        return {header_name: token}
    return {}


def _url_to_server_key(url: str) -> str:
    """MCP 서버 URL을 고유 키로 변환 (호스트 + 경로 포함)."""
    parsed = urlparse(url)
    key = parsed.netloc + parsed.path.rstrip("/")
    return key.replace(".", "_").replace(":", "_").replace("/", "_")


def _create_mcp_error_stub(name: str) -> BaseTool:
    """MCP 서버 연결 실패 시 에러를 반환하는 stub 도구."""
    from langchain_core.tools import StructuredTool

    async def _call(**kwargs: Any) -> str:
        return f"MCP tool '{name}' is temporarily unavailable. Please try again later."

    return StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=f"MCP tool (currently unavailable): {name}",
    )


async def _build_mcp_tools(mcp_configs: list[dict]) -> list[BaseTool]:
    """MCP 도구를 langchain-mcp-adapters로 생성."""
    if not mcp_configs:
        return []

    from langchain_mcp_adapters.client import MultiServerMCPClient

    # 1. MCP 서버 URL별로 그룹화 — 서버 키 기준으로 도구 필터링
    servers: dict[str, dict] = {}
    tool_filter: dict[str, set[str]] = {}  # server_key → {tool_names}

    for tc in mcp_configs:
        url = tc["mcp_server_url"]
        tool_name = tc.get("mcp_tool_name", tc["name"])
        key = _url_to_server_key(url)

        if key not in servers:
            headers = _auth_config_to_headers(tc.get("auth_config"))
            servers[key] = {
                "transport": "streamable_http",
                "url": url,
                "headers": headers or None,
            }
            tool_filter[key] = set()

        tool_filter[key].add(tool_name)

    # 2. 서버별로 도구 로딩 + 필터링 — (tool, origin) 쌍으로 추적
    collected: list[tuple[BaseTool, str]] = []

    for key, config in servers.items():
        try:
            client = MultiServerMCPClient({key: config})
            server_tools = await client.get_tools()
            needed = tool_filter[key]
            for t in server_tools:
                if t.name in needed:
                    collected.append((t, key))
        except Exception:
            logger.warning("MCP tool loading failed for %s", key, exc_info=True)
            for tool_name in tool_filter[key]:
                collected.append((_create_mcp_error_stub(tool_name), key))

    # 3. 중복 이름 disambiguation — 서버 키를 prefix로 추가
    name_counts: dict[str, int] = {}
    for tool, _ in collected:
        name_counts[tool.name] = name_counts.get(tool.name, 0) + 1

    if any(c > 1 for c in name_counts.values()):
        for tool, origin in collected:
            if name_counts.get(tool.name, 0) > 1:
                tool.name = f"{origin}_{tool.name}"

    return [tool for tool, _ in collected]


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

    # 1. 도구 생성 — builtin/prebuilt/custom은 기존 방식 유지
    langchain_tools: list[BaseTool] = []
    mcp_configs: list[dict] = []

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
            mcp_configs.append(tc)
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

    # 2. MCP 도구 — langchain-mcp-adapters 사용
    mcp_tools = await _build_mcp_tools(mcp_configs)
    langchain_tools.extend(mcp_tools)

    # 3. 미들웨어 — 기존 방식 유지
    middleware = build_middleware_instances(middleware_configs or [])
    middleware += get_provider_middleware(provider)

    # 4. 에이전트 빌드 — create_deep_agent + checkpointer
    from app.agent_runtime.checkpointer import get_checkpointer

    agent = build_agent(
        model,
        langchain_tools,
        system_prompt,
        middleware=middleware or None,
        checkpointer=get_checkpointer(),
        name=f"agent_{thread_id[:8]}",
    )

    # 5. 스트리밍
    lc_messages = convert_to_langchain_messages(messages_history)
    config = {"configurable": {"thread_id": thread_id}}

    async for chunk in stream_agent_response(agent, lc_messages, config):
        yield chunk
