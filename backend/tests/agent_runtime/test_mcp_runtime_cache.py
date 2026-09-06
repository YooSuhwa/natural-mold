"""Runtime MCP tool cache tests."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import ToolNode

from app.mcp.domain import McpAppRuntimeContext


def _tool(name: str):
    async def _call(**kwargs: Any) -> str:
        return "ok"

    return StructuredTool.from_function(coroutine=_call, name=name, description=name)


@pytest.mark.asyncio
async def test_build_mcp_tools_reuses_cached_server_tools(monkeypatch) -> None:
    from app.agent_runtime import mcp_cache
    from app.agent_runtime.mcp_tool_loader import _build_mcp_tools

    await mcp_cache.clear_mcp_tool_cache()
    calls: list[dict[str, Any]] = []

    class FakeMCPClient:
        def __init__(self, servers: dict[str, Any], **kwargs: Any) -> None:
            calls.append({"servers": servers, "kwargs": kwargs})

        async def get_tools(self):
            return [_tool("echo")]

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient",
        FakeMCPClient,
    )

    configs = [
        {
            "definition_key": "mcp",
            "name": "echo",
            "mcp_server_url": "https://mcp.example.com",
            "mcp_tool_name": "echo",
            "mcp_transport_headers": {"Authorization": "Bearer abc"},
        }
    ]

    first = await _build_mcp_tools(configs)
    second = await _build_mcp_tools(configs)

    assert [tool.name for tool in first] == ["echo"]
    assert [tool.name for tool in second] == ["echo"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_mcp_tool_with_retry_returns_error_string_on_final_failure() -> None:
    from app.agent_runtime.mcp_cache import MCPToolWithRetry

    attempts = 0

    async def _call(**kwargs: Any) -> str:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("slow")

    wrapped = MCPToolWithRetry(
        _tool("unstable"),
        max_retries=2,
        retry_delay=0,
        timeout_seconds=0.01,
    )
    object.__setattr__(wrapped, "_original_tool", _tool("unstable"))
    wrapped._original_tool.coroutine = _call  # type: ignore[method-assign]

    result = await wrapped.ainvoke({})

    assert attempts == 2
    assert "[MCP Tool Error]" in result
    assert "unstable" in result


@pytest.mark.asyncio
async def test_mcp_tool_with_retry_preserves_content_and_artifact_after_retry() -> None:
    from app.agent_runtime.mcp_cache import MCPToolWithRetry

    attempts = 0

    async def _call(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("transient")
        return "rendered", {"structured_content": {"temperature": 72}}

    original = StructuredTool.from_function(
        coroutine=_call,
        name="weather",
        description="weather",
        response_format="content_and_artifact",
    )
    wrapped = MCPToolWithRetry(
        original,
        max_retries=2,
        retry_delay=0,
        timeout_seconds=1,
    )

    result = await wrapped.ainvoke(
        {"name": "weather", "args": {}, "id": "tool-call-1", "type": "tool_call"}
    )

    assert attempts == 2
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "tool-call-1"
    assert result.content == "rendered"
    assert result.artifact == {"structured_content": {"temperature": 72}}


@pytest.mark.asyncio
async def test_mcp_app_timeout_does_not_repeat_accepted_side_effect() -> None:
    from app.agent_runtime.mcp_cache import MCPToolWithRetry

    attempts = 0

    async def _call(**_kwargs: Any) -> tuple[str, dict[str, Any]]:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.2)
        return "late", {"structured_content": {"attempts": attempts}}

    original = StructuredTool.from_function(
        coroutine=_call,
        name="side_effect",
        description="side effect",
        response_format="content_and_artifact",
    )
    ids = [uuid.uuid4() for _ in range(6)]
    context = McpAppRuntimeContext(
        conversation_id=ids[0],
        user_id=ids[1],
        agent_id=ids[2],
        credential_subject_user_id=ids[3],
        mcp_server_id=ids[4],
        mcp_tool_id=ids[5],
        remote_tool_name="side_effect",
        resource_uri="ui://side-effect/app",
        tool_meta={"ui": {"resourceUri": "ui://side-effect/app"}},
    )
    wrapped = MCPToolWithRetry(
        original,
        max_retries=3,
        retry_delay=0,
        timeout_seconds=0.01,
        mcp_app_context=context,
    )

    result = await wrapped.ainvoke({})

    assert attempts == 1
    assert "TimeoutError" in str(result)


@pytest.mark.asyncio
async def test_mcp_app_binding_is_minted_from_actual_toolnode_call() -> None:
    from app.agent_runtime.mcp_cache import _MCP_RESULT_ENVELOPE, MCPToolWithRetry

    ids = [uuid.uuid4() for _ in range(6)]
    context = McpAppRuntimeContext(
        conversation_id=ids[0],
        user_id=ids[1],
        agent_id=ids[2],
        credential_subject_user_id=ids[3],
        mcp_server_id=ids[4],
        mcp_tool_id=ids[5],
        remote_tool_name="weather",
        resource_uri="ui://weather/dashboard",
        tool_meta={"ui": {"resourceUri": "ui://weather/dashboard"}},
    )
    recorded: dict[str, Any] = {}

    async def _call(**_kwargs: Any) -> tuple[str, dict[str, Any]]:
        return (
            "sunny",
            {
                "structured_content": {
                    _MCP_RESULT_ENVELOPE: {
                        "structured_content": {"temperature": 72},
                        "result_meta": {
                            "viewUUID": "view-1",
                            "opaque": "runtime-known-secret",
                            "access_token": "drop",
                        },
                    }
                }
            },
        )

    async def _record(
        received_context: McpAppRuntimeContext,
        run_id: str,
        tool_call_id: str,
        result_meta: dict[str, Any] | None,
        structured_content: dict[str, Any] | None,
    ) -> str:
        recorded.update(
            context=received_context,
            run_id=run_id,
            tool_call_id=tool_call_id,
            result_meta=result_meta,
            structured_content=structured_content,
        )
        return "11111111-1111-1111-1111-111111111111"

    original = StructuredTool.from_function(
        coroutine=_call,
        name="weather",
        description="weather",
        response_format="content_and_artifact",
    )
    wrapped = MCPToolWithRetry(
        original,
        retry_delay=0,
        mcp_app_context=context,
        binding_recorder=_record,
    )
    node = ToolNode([wrapped])
    run_id = str(uuid.uuid4())

    from app.agent_runtime.run_secrets import reset_run_secrets, set_run_secrets

    token = set_run_secrets(["runtime-known-secret"])
    try:
        from langchain.tools import ToolRuntime
        from langgraph._internal._constants import CONFIG_KEY_RUNTIME

        config = {"configurable": {"moldy_run_id": run_id}}
        runtime = ToolRuntime(
            state={},
            context=None,
            config=config,
            stream_writer=lambda _chunk: None,
            tool_call_id=None,
            store=None,
            tools=[wrapped],
        )
        config["configurable"][CONFIG_KEY_RUNTIME] = runtime
        state = await node.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "weather",
                                "args": {},
                                "id": "actual-call",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            },
            config=config,
        )
    finally:
        reset_run_secrets(token)

    assert state["messages"][0].status == "success", state
    assert recorded, state["messages"][0]
    assert recorded["tool_call_id"] == "actual-call"
    assert recorded["context"] == context
    assert recorded["result_meta"] == {"viewUUID": "view-1", "opaque": "<redacted>"}
    assert recorded["structured_content"] == {"temperature": 72}
    message = state["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "actual-call"
    assert message.content == "sunny"
    assert message.artifact == {
        "structured_content": {"temperature": 72},
        "mcp_app": {
            "version": 1,
            "binding_id": "11111111-1111-1111-1111-111111111111",
            "run_id": run_id,
            "resource_uri": "ui://weather/dashboard",
            "tool_meta": {"ui": {"resourceUri": "ui://weather/dashboard"}},
            "result_meta": {"viewUUID": "view-1", "opaque": "<redacted>"},
        },
    }


@pytest.mark.asyncio
async def test_result_meta_interceptor_carries_bounded_meta_in_adapter_artifact() -> None:
    from mcp.types import CallToolResult, TextContent

    from app.agent_runtime.mcp_app_runtime import ResultMetaInterceptor
    from app.agent_runtime.mcp_cache import _MCP_RESULT_ENVELOPE

    async def _handler(_request: Any) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text="sunny")],
            structuredContent={"temperature": 72},
            _meta={"viewUUID": "view-1"},
        )

    result = await ResultMetaInterceptor()(SimpleNamespace(), _handler)

    assert result.structuredContent == {
        _MCP_RESULT_ENVELOPE: {
            "structured_content": {"temperature": 72},
            "result_meta": {"viewUUID": "view-1"},
        }
    }


@pytest.mark.asyncio
async def test_build_mcp_tools_uses_live_apps_metadata_and_hides_app_only_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime import mcp_cache
    from app.agent_runtime.mcp_tool_loader import _build_mcp_tools

    await mcp_cache.clear_mcp_tool_cache()
    visible = _tool("weather")
    visible.metadata = {"_meta": {"ui": {"resourceUri": "ui://weather/dashboard"}}}
    app_only = _tool("refresh_weather")
    app_only.metadata = {"_meta": {"ui": {"visibility": ["app"]}}}

    class FakeMCPClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def get_tools(self) -> list[StructuredTool]:
            return [visible, app_only]

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient",
        FakeMCPClient,
    )
    ids = [str(uuid.uuid4()) for _ in range(6)]
    base = {
        "definition_key": "mcp",
        "mcp_server_url": "https://mcp.example.test/rpc",
        "conversation_id": ids[0],
        "user_id": ids[1],
        "agent_id": ids[2],
        "credential_subject_user_id": ids[3],
        "mcp_server_id": ids[4],
    }
    configs = [
        {**base, "name": "weather", "mcp_tool_name": "weather", "mcp_tool_id": ids[5]},
        {
            **base,
            "name": "refresh_weather",
            "mcp_tool_name": "refresh_weather",
            "mcp_tool_id": str(uuid.uuid4()),
        },
    ]

    tools = await _build_mcp_tools(configs)

    assert [tool.name for tool in tools] == ["weather"]
    assert tools[0]._mcp_app_context == McpAppRuntimeContext(  # type: ignore[attr-defined]
        conversation_id=uuid.UUID(ids[0]),
        user_id=uuid.UUID(ids[1]),
        agent_id=uuid.UUID(ids[2]),
        credential_subject_user_id=uuid.UUID(ids[3]),
        mcp_server_id=uuid.UUID(ids[4]),
        mcp_tool_id=uuid.UUID(ids[5]),
        remote_tool_name="weather",
        resource_uri="ui://weather/dashboard",
        tool_meta={"ui": {"resourceUri": "ui://weather/dashboard"}},
    )
