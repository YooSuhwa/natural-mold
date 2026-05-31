"""Tests for app.agent_runtime.executor — agent building and stream orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_runtime.executor import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.tools.risk import default_deepagents_interrupt_policy

TEMPORAL_TOOL_NAMES = {"current_datetime", "resolve_relative_date"}


def _expected_interrupt_policy() -> dict:
    return {
        **default_deepagents_interrupt_policy(),
        "ask_user": {"allowed_decisions": ["respond"]},
    }


def _cfg(**overrides) -> AgentConfig:
    """테스트용 AgentConfig 기본값 생성."""
    defaults: dict[str, object] = dict(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        thread_id="t-1",
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


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
        permissions=None,
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
def test_build_agent_passes_permissions(mock_create: MagicMock):
    from deepagents.middleware.filesystem import FilesystemPermission

    from app.agent_runtime.executor import build_agent

    permissions = [
        FilesystemPermission(
            operations=["read"],
            paths=["/runtime/t-1/skills/**"],
        )
    ]

    build_agent(MagicMock(), [], "prompt", permissions=permissions)

    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["permissions"] == permissions


@patch("app.agent_runtime.executor.create_deep_agent")
def test_build_agent_returns_agent(mock_create: MagicMock):
    from app.agent_runtime.executor import build_agent

    sentinel = MagicMock()
    mock_create.return_value = sentinel

    result = build_agent(MagicMock(), [], "prompt")
    assert result is sentinel


# ---------------------------------------------------------------------------
# middleware model credential boundary
# ---------------------------------------------------------------------------


def test_resolve_middleware_model_requires_user_key():
    from app.agent_runtime.executor import (
        MiddlewareModelCredentialRequiredError,
        _resolve_middleware_model_params,
    )

    configs = [
        {
            "type": "summarization",
            "params": {"model": "openai:gpt-4o-mini"},
        }
    ]

    with pytest.raises(MiddlewareModelCredentialRequiredError) as exc:
        _resolve_middleware_model_params(configs, {})

    assert exc.value.code == "middleware_model_credential_required"
    assert exc.value.status == 422


@patch("app.agent_runtime.executor.create_chat_model")
def test_resolve_middleware_model_uses_user_key_without_env_fallback(
    mock_model_factory: MagicMock,
):
    from app.agent_runtime.executor import _resolve_middleware_model_params

    sentinel = MagicMock()
    mock_model_factory.return_value = sentinel
    configs = [
        {
            "type": "summarization",
            "params": {"model": "openai:gpt-4o-mini"},
        }
    ]

    result = _resolve_middleware_model_params(configs, {"openai": "sk-user"})

    mock_model_factory.assert_called_once_with(
        "openai",
        "gpt-4o-mini",
        api_key="sk-user",
        allow_env_fallback=False,
    )
    assert result[0]["params"]["model"] is sentinel


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
    cfg = _cfg(api_key="sk-test", system_prompt="Hello", thread_id="thread-1")
    async for chunk in execute_agent_stream(cfg, []):
        chunks.append(chunk)

    mock_model_factory.assert_called_once_with("openai", "gpt-4o", "sk-test", None)
    mock_build.assert_called_once()
    tools_passed = mock_build.call_args[0][1]
    tool_names = {tool.name for tool in tools_passed}
    assert TEMPORAL_TOOL_NAMES.issubset(tool_names)
    assert "ask_user" in tool_names

    system_prompt = mock_build.call_args[0][2]
    assert "현재 기준 날짜와 시간" in system_prompt


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_execute_stream_runtime_tool_called_per_entry(
    mock_factory: MagicMock,
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
    mock_factory.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {
            "tool_id": "1",
            "definition_key": "builtin:web_search",
            "name": "Web Search",
            "description": "search",
            "parameters": {},
            "credentials": None,
            "credential_id": None,
        },
        {
            "tool_id": "2",
            "definition_key": "naver_search_blog",
            "name": "Naver Blog Search",
            "description": "blog",
            "parameters": {"query": "x"},
            "credentials": {"client_id": "a", "client_secret": "b"},
            "credential_id": "cred-1",
        },
    ]

    async for _ in execute_agent_stream(_cfg(tools_config=tools_config), []):
        pass

    assert mock_factory.call_count == 2
    tools_passed = mock_build.call_args[0][1]
    # 2 user tools + temporal helpers + ask_user auto-injected helper
    assert len(tools_passed) == 5


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_execute_stream_skips_unknown_tool(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """When the factory returns ``None`` (unknown definition_key), the
    executor must skip it instead of crashing the chat session."""

    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    mock_factory.return_value = None

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {
            "tool_id": "1",
            "definition_key": "unknown:thing",
            "name": "Mystery",
            "description": "?",
            "parameters": {},
            "credentials": None,
            "credential_id": None,
        }
    ]

    async for _ in execute_agent_stream(_cfg(tools_config=tools_config), []):
        pass

    tools_passed = mock_build.call_args[0][1]
    tool_names = {tool.name for tool in tools_passed}
    assert TEMPORAL_TOOL_NAMES.issubset(tool_names)
    assert "ask_user" in tool_names


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

    async for _ in execute_agent_stream(_cfg(tools_config=tools_config), []):
        pass

    tools_passed = mock_build.call_args[0][1]
    tool_names = {tool.name for tool in tools_passed}
    assert TEMPORAL_TOOL_NAMES.issubset(tool_names)
    assert "ask_user" in tool_names


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
        _cfg(thread_id="my-thread-42"),
        [{"role": "user", "content": "hi"}],
    ):
        pass

    assert captured_config["configurable"]["thread_id"] == "my-thread-42"


@pytest.mark.asyncio
async def test_execute_stream_attaches_langfuse_trace_context(monkeypatch):
    from app.agent_runtime.executor import execute_agent_stream

    async def fake_prepare_agent(*args, **kwargs):
        return MagicMock(), [], {"configurable": {"thread_id": "conv-123"}}

    captured_config = {}

    async def fake_stream(_agent, _messages, config, **_kwargs):
        captured_config.update(config)
        yield "done"

    class FakeLangfuseContext:
        trace = SimpleNamespace(
            provider="langfuse",
            trace_id="lf-trace-run-123",
            trace_url="https://langfuse.local/project/moldy/traces/lf-trace-run-123",
        )
        metadata = {
            "moldy_run_id": "run-123",
            "moldy_source": "chat",
        }

        def configure_config(self, config):
            return {
                **config,
                "callbacks": ["langfuse-callback"],
                "metadata": self.metadata,
                "tags": ["moldy", "source:chat"],
            }

        def flush(self):
            pass

    monkeypatch.setattr("app.agent_runtime.executor._prepare_agent", fake_prepare_agent)
    monkeypatch.setattr("app.agent_runtime.executor.stream_agent_response", fake_stream)
    monkeypatch.setattr(
        "app.agent_runtime.executor.build_langfuse_run_context",
        lambda *_args, **_kwargs: FakeLangfuseContext(),
    )

    langfuse_sink = []
    async for _ in execute_agent_stream(
        _cfg(agent_id="agent-123", user_id="user-123"),
        [{"role": "user", "content": "hi"}],
        run_id="run-123",
        moldy_source="chat",
        langfuse_sink=langfuse_sink,
    ):
        pass

    assert captured_config["configurable"]["thread_id"] == "conv-123"
    assert captured_config["callbacks"] == ["langfuse-callback"]
    assert captured_config["metadata"]["moldy_run_id"] == "run-123"
    assert captured_config["tags"] == ["moldy", "source:chat"]
    assert langfuse_sink[0].trace_id == "lf-trace-run-123"


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

    async for _ in execute_agent_stream(_cfg(tools_config=tools_config), []):
        pass

    tools_passed = mock_build.call_args[0][1]
    tool_names = {tool.name for tool in tools_passed}
    assert TEMPORAL_TOOL_NAMES.issubset(tool_names)
    assert "ask_user" in tool_names


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
    async for chunk in execute_agent_stream(_cfg(), []):
        chunks.append(chunk)

    assert chunks == ["chunk-1", "chunk-2", "chunk-3"]


@pytest.mark.asyncio
async def test_execute_stream_records_stream_error_as_hook_failure(monkeypatch):
    """A stream-visible error must call hook failure, not hook post success."""
    from app.agent_runtime.executor import execute_agent_stream

    class FailingAgent:
        async def astream(self, *args, **kwargs):
            yield (MagicMock(content="partial", type="ai", tool_calls=[]), {})
            raise RuntimeError("provider stream failed")

        async def aget_state(self, *args, **kwargs):
            state = MagicMock()
            state.tasks = []
            return state

    async def fake_prepare_agent(*args, **kwargs):
        return FailingAgent(), [], {"configurable": {"thread_id": "t-1"}}

    mock_hooks = MagicMock()
    mock_hooks.run_pre = AsyncMock()
    mock_hooks.run_post = AsyncMock()
    mock_hooks.run_failure = AsyncMock()

    monkeypatch.setattr(
        "app.agent_runtime.executor._prepare_agent",
        fake_prepare_agent,
    )
    monkeypatch.setattr("app.agent_runtime.executor.hooks", mock_hooks)

    errors: list[StreamErrorRecord] = []
    chunks = [
        chunk
        async for chunk in execute_agent_stream(
            _cfg(
                user_id="00000000-0000-0000-0000-000000000001",
                agent_id="00000000-0000-0000-0000-0000000000aa",
            ),
            [{"role": "user", "content": "hi"}],
            error_sink=errors,
        )
    ]

    assert any("event: error" in chunk for chunk in chunks)
    assert len(errors) == 1
    assert str(errors[0].error) == "provider stream failed"
    mock_hooks.run_pre.assert_awaited_once()
    mock_hooks.run_failure.assert_awaited_once()
    failure_error = mock_hooks.run_failure.await_args.args[1]
    assert isinstance(failure_error, RuntimeError)
    assert str(failure_error) == "provider stream failed"
    mock_hooks.run_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_stream_records_stream_error_in_sink(monkeypatch):
    """Resume streams must expose the same typed error signal as normal streams."""
    from app.agent_runtime.executor import resume_agent_stream

    class FailingAgent:
        async def astream(self, *args, **kwargs):
            raise RuntimeError("resume stream failed")
            yield

        async def aget_state(self, *args, **kwargs):
            state = MagicMock()
            state.tasks = []
            return state

    async def fake_prepare_agent(*args, **kwargs):
        return FailingAgent(), [], {"configurable": {"thread_id": "t-1"}}

    mock_hooks = MagicMock()
    mock_hooks.run_pre = AsyncMock()
    mock_hooks.run_post = AsyncMock()
    mock_hooks.run_failure = AsyncMock()

    monkeypatch.setattr(
        "app.agent_runtime.executor._prepare_agent",
        fake_prepare_agent,
    )
    monkeypatch.setattr("app.agent_runtime.executor.hooks", mock_hooks)

    errors: list[StreamErrorRecord] = []
    chunks = [
        chunk
        async for chunk in resume_agent_stream(
            _cfg(
                user_id="00000000-0000-0000-0000-000000000001",
                agent_id="00000000-0000-0000-0000-0000000000aa",
            ),
            {"answer": "approved"},
            error_sink=errors,
        )
    ]

    assert any("event: error" in chunk for chunk in chunks)
    assert len(errors) == 1
    assert str(errors[0].error) == "resume stream failed"
    mock_hooks.run_failure.assert_awaited_once()
    mock_hooks.run_post.assert_not_awaited()


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
            _cfg(
                agent_skills=agent_skills,
                agent_id="agent-123",
                user_id="00000000-0000-0000-0000-000000000001",
            ),
            [],
        ):
            pass

    build_kwargs = mock_build.call_args[1]
    # ADR-017 Slice E (2026-05-19) — skill mount moved from the global
    # ``/skills/`` to a per-thread ``/runtime/<thread_id>/skills/`` so
    # one agent can never see another agent's selected skills
    # (Spec §9). The ``_cfg`` fixture uses ``thread_id="t-1"``.
    assert build_kwargs["skills"] == ["/runtime/t-1/skills/"]
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

    async for _ in execute_agent_stream(_cfg(), []):
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
        _cfg(
            tools_config=[{"type": "builtin", "name": "Web Search"}],
            middleware_configs=[{"type": "human_in_the_loop", "params": {}}],
        ),
        [],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    # Read-only tools do not add approval, but interactive agents still wrap
    # ask_user and DeepAgents write tools through the standard policy.
    assert build_kwargs["interrupt_on"] == _expected_interrupt_policy()


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
    """No HiTL middleware → ask_user still gets standard respond interrupt policy."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(
        _cfg(middleware_configs=[{"type": "tool_retry", "params": {}}]),
        [],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["interrupt_on"] == _expected_interrupt_policy()


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
    mock_agent.ainvoke = AsyncMock(
        return_value={"messages": [MagicMock(content="Hello", type="ai")]}
    )
    mock_build.return_value = mock_agent

    await execute_agent_invoke(_cfg(), [])

    tools_passed = mock_build.call_args[0][1]
    tool_names = [t.name for t in tools_passed]
    assert "ask_user" not in tool_names
    assert TEMPORAL_TOOL_NAMES.issubset(set(tool_names))


@pytest.mark.asyncio
async def test_execute_agent_invoke_attaches_langfuse_trace_context(monkeypatch):
    from app.agent_runtime.executor import execute_agent_invoke

    class FakeAgent:
        config: dict | None = None

        async def ainvoke(self, _payload, *, config):
            self.config = config
            return {"messages": [SimpleNamespace(content="trigger output")]}

    fake_agent = FakeAgent()

    async def fake_prepare_agent(*args, **kwargs):
        return fake_agent, ["msg"], {"configurable": {"thread_id": "conv-trigger"}}

    class FakeLangfuseContext:
        trace = SimpleNamespace(
            provider="langfuse",
            trace_id="lf-trace-trigger",
            trace_url="https://langfuse.local/project/moldy/traces/lf-trace-trigger",
        )
        metadata = {"moldy_run_id": "trigger-run-1", "moldy_source": "trigger"}

        def configure_config(self, config):
            return {
                **config,
                "callbacks": ["langfuse-callback"],
                "metadata": self.metadata,
                "tags": ["moldy", "source:trigger"],
            }

        def flush(self):
            pass

    monkeypatch.setattr("app.agent_runtime.executor._prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(
        "app.agent_runtime.executor.build_langfuse_run_context",
        lambda *_args, **_kwargs: FakeLangfuseContext(),
    )

    output = await execute_agent_invoke(
        _cfg(agent_id="agent-123", user_id="user-123"),
        [{"role": "user", "content": "scheduled"}],
        run_id="trigger-run-1",
        moldy_source="trigger",
    )

    assert output == "trigger output"
    assert fake_agent.config is not None
    assert fake_agent.config["callbacks"] == ["langfuse-callback"]
    assert fake_agent.config["metadata"]["moldy_run_id"] == "trigger-run-1"
