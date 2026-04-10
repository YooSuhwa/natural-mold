"""Tests for app.agent_runtime.executor — agent building and stream orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# build_agent
# ---------------------------------------------------------------------------


@patch("app.agent_runtime.executor.create_deep_agent")
def test_build_agent_calls_deep_agent(mock_create: MagicMock):
    from app.agent_runtime.executor import build_agent

    mock_model = MagicMock()
    mock_tools = [MagicMock(), MagicMock()]

    build_agent(mock_model, mock_tools, "You are helpful.")  # type: ignore[arg-type]

    mock_create.assert_called_once_with(
        model=mock_model,
        tools=mock_tools,
        system_prompt="You are helpful.",
        middleware=(),
        interrupt_on=None,
        checkpointer=None,
        store=None,
        backend=None,
        skills=None,
        memory=None,
        name=None,
    )


@patch("app.agent_runtime.executor.create_deep_agent")
def test_build_agent_passes_skills_and_memory(mock_create: MagicMock):
    from app.agent_runtime.executor import build_agent

    mock_model = MagicMock()
    mock_backend = MagicMock()

    build_agent(
        mock_model,
        [],
        "prompt",
        backend=mock_backend,
        skills=["/skills/"],
        memory=["/agents/abc/AGENTS.md"],
    )

    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["skills"] == ["/skills/"]
    assert call_kwargs["memory"] == ["/agents/abc/AGENTS.md"]
    assert call_kwargs["backend"] is mock_backend


@patch("app.agent_runtime.executor.create_deep_agent")
def test_build_agent_returns_agent(mock_create: MagicMock):
    from app.agent_runtime.executor import build_agent

    sentinel = MagicMock()
    mock_create.return_value = sentinel

    result = build_agent(MagicMock(), [], "prompt")
    assert result is sentinel


# ---------------------------------------------------------------------------
# execute_agent_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_execute_stream_no_tools(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "event: message_end\ndata: {}\n\n"

    mock_stream.return_value = fake_stream()

    chunks = []
    async for chunk in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key="sk-test",
        base_url=None,
        system_prompt="Hello",
        tools_config=[],
        messages_history=[],
        thread_id="thread-1",
    ):
        chunks.append(chunk)

    mock_model_factory.assert_called_once_with("openai", "gpt-4o", "sk-test", None)
    mock_build.assert_called_once()
    # Only ask_user tool should be present (auto-included)
    tools_passed = mock_build.call_args[0][1]
    # Only ask_user tool should be present (auto-included)
    assert len(tools_passed) == 1
    assert tools_passed[0].name == "ask_user"


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_builtin_tool")
async def test_execute_stream_builtin_tool(
    mock_builtin: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    mock_builtin.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [{"type": "builtin", "name": "Web Search"}]

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=tools_config,
        messages_history=[],
        thread_id="t-1",
    ):
        pass

    mock_builtin.assert_called_once_with("Web Search")
    tools_passed = mock_build.call_args[0][1]
    assert len(tools_passed) == 2  # builtin + ask_user (auto)


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_prebuilt_tool")
async def test_execute_stream_prebuilt_tool(
    mock_prebuilt: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    mock_prebuilt.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    auth = {"naver_client_id": "id"}
    tools_config = [{"type": "prebuilt", "name": "Naver Blog Search", "auth_config": auth}]

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=tools_config,
        messages_history=[],
        thread_id="t-1",
    ):
        pass

    mock_prebuilt.assert_called_once_with("Naver Blog Search", auth_config=auth)


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_from_db")
async def test_execute_stream_custom_tool(
    mock_custom: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    mock_custom.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {
            "type": "custom",
            "name": "My API",
            "description": "desc",
            "api_url": "https://api.example.com",
            "http_method": "POST",
            "parameters_schema": None,
            "auth_type": "api_key",
            "auth_config": {"header_name": "X-Key", "api_key": "k"},
        }
    ]

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=tools_config,
        messages_history=[],
        thread_id="t-1",
    ):
        pass

    mock_custom.assert_called_once_with(
        name="My API",
        description="desc",
        api_url="https://api.example.com",
        http_method="POST",
        parameters_schema=None,
        auth_type="api_key",
        auth_config={"header_name": "X-Key", "api_key": "k"},
    )


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_builtin_tool")
@patch("app.agent_runtime.executor.create_prebuilt_tool")
async def test_execute_stream_mixed_tools(
    mock_prebuilt: MagicMock,
    mock_builtin: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    mock_builtin.return_value = MagicMock()
    mock_prebuilt.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {"type": "builtin", "name": "Web Search"},
        {"type": "prebuilt", "name": "Naver Blog Search"},
    ]

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=tools_config,
        messages_history=[],
        thread_id="t-1",
    ):
        pass

    # Both tool factories should be called
    mock_builtin.assert_called_once_with("Web Search")
    mock_prebuilt.assert_called_once()
    tools_passed = mock_build.call_args[0][1]
    assert len(tools_passed) == 3  # 2 user tools + ask_user (auto)


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_execute_stream_skips_custom_without_api_url(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """Custom tool without api_url should be silently skipped."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {"type": "custom", "name": "No URL Tool"}  # missing api_url
    ]

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=tools_config,
        messages_history=[],
        thread_id="t-1",
    ):
        pass

    tools_passed = mock_build.call_args[0][1]
    assert len(tools_passed) == 1  # ask_user only (auto)


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_execute_stream_passes_thread_id_in_config(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = ["msg"]
    mock_build.return_value = MagicMock()

    captured_config = {}

    async def capture_stream(agent, messages, config, **_kwargs):
        captured_config.update(config)
        yield "done"

    mock_stream.side_effect = capture_stream

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        messages_history=[{"role": "user", "content": "hi"}],
        thread_id="my-thread-42",
    ):
        pass

    assert captured_config["configurable"]["thread_id"] == "my-thread-42"


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_execute_stream_skips_unknown_tool_type(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """Unknown tool types should be silently ignored."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {"type": "mcp", "name": "Some MCP Tool"}  # not handled in executor
    ]

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=tools_config,
        messages_history=[],
        thread_id="t-1",
    ):
        pass

    tools_passed = mock_build.call_args[0][1]
    assert len(tools_passed) == 1  # ask_user only (auto)


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_execute_stream_yields_chunks(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """Verify that all chunks from stream_agent_response are yielded."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "chunk-1"
        yield "chunk-2"
        yield "chunk-3"

    mock_stream.return_value = fake_stream()

    chunks = []
    async for chunk in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        messages_history=[],
        thread_id="t-1",
    ):
        chunks.append(chunk)

    assert chunks == ["chunk-1", "chunk-2", "chunk-3"]


@pytest.mark.asyncio
@patch("app.agent_runtime.executor.FilesystemBackend")
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_execute_stream_passes_skills_and_memory(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
    tmp_path,
):
    """Skills and memory params are forwarded to build_agent when provided."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    agent_skills = [{"skill_id": "s1", "storage_path": "/data/skills/s1"}]

    mock_data_dir = tmp_path / "data"
    mock_data_dir.mkdir(exist_ok=True)

    with patch("app.agent_runtime.executor._DATA_DIR", mock_data_dir):
        async for _ in execute_agent_stream(
            provider="openai",
            model_name="gpt-4o",
            api_key=None,
            base_url=None,
            system_prompt="Hi",
            tools_config=[],
            messages_history=[],
            thread_id="t-1",
            agent_skills=agent_skills,
            agent_id="agent-123",
        ):
            pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["skills"] == ["/skills/"]
    assert build_kwargs["memory"] == ["/agents/agent-123/AGENTS.md"]
    assert build_kwargs["backend"] is mock_fs_backend_cls.return_value

    # Verify agent directory was created
    assert (mock_data_dir / "agents" / "agent-123").exists()


@pytest.mark.asyncio
@patch("app.agent_runtime.executor.FilesystemBackend")
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_execute_stream_no_skills_no_memory_when_not_provided(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
):
    """When agent_skills and agent_id are not provided, skills/memory should be None."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        messages_history=[],
        thread_id="t-1",
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["skills"] is None
    assert build_kwargs["memory"] is None


# ---------------------------------------------------------------------------
# interrupt_on extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_interrupt_on_with_write_tools(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """HiTL middleware + write tool → interrupt_on에 해당 도구 포함."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[{"type": "builtin", "name": "Web Search"}],
        messages_history=[],
        thread_id="t-1",
        middleware_configs=[{"type": "human_in_the_loop", "params": {}}],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    # interrupt_on should be None — "Web Search" doesn't match _WRITE_TOOL_KEYWORDS
    assert build_kwargs["interrupt_on"] is None


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_interrupt_on_without_hitl_middleware(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """No HiTL middleware → interrupt_on is None."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        messages_history=[],
        thread_id="t-1",
        middleware_configs=[{"type": "tool_retry", "params": {}}],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["interrupt_on"] is None


@pytest.mark.asyncio
@patch("app.agent_runtime.executor.FilesystemBackend")
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_ask_user_not_included_in_invoke(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
):
    """execute_agent_invoke should NOT include ask_user tool."""
    from app.agent_runtime.executor import execute_agent_invoke

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []

    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(content="Hello", type="ai")]
    })
    mock_build.return_value = mock_agent

    await execute_agent_invoke(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        messages_history=[],
        thread_id="t-1",
    )

    tools_passed = mock_build.call_args[0][1]
    tool_names = [t.name for t in tools_passed]
    assert "ask_user" not in tool_names
