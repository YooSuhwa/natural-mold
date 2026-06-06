"""Tests for app.agent_runtime.executor — agent building and stream orchestration."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from app.agent_runtime.executor import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.marketplace.skill_runtime import SkillToolContext
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


def _deep_research_skill() -> dict[str, object]:
    return {
        "id": "00000000-0000-0000-0000-0000000000d1",
        "slug": "deep-research",
        "name": "Deep Research",
        "description": "Deep research",
        "storage_path": "skills/deep-research",
        "execution_profile": {"tool_dependencies": ["tavily_search"]},
    }


def _stub_skill_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "app.agent_runtime.executor.build_skill_runtime_context",
        lambda *_args, **_kwargs: SkillToolContext(
            thread_id="t-1",
            output_dir=tmp_path / "outputs",
            runtime_root=tmp_path / "runtime",
            descriptors={},
        ),
    )


def _capture_runtime_tool_configs(captured: list[dict[str, object]]):
    def _factory(config: dict[str, object]):
        captured.append(dict(config))
        tool = MagicMock()
        tool.name = str(config.get("name"))
        return tool

    return _factory


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
        subagents=None,
    )


@patch("app.agent_runtime.executor.create_deep_agent")
def test_build_agent_forwards_subagents_to_deep_agents(mock_create: MagicMock):
    from app.agent_runtime.executor import build_agent

    subagents = [{"name": "agent_abcd1234", "description": "helper"}]

    build_agent(
        MagicMock(),
        [],
        "You are helpful.",
        name="agent_parent12",
        subagents=subagents,
    )  # type: ignore[arg-type]

    assert mock_create.call_args.kwargs["name"] == "agent_parent12"
    assert mock_create.call_args.kwargs["subagents"] == subagents


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


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_prepare_agent_logs_timing_spans(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_checkpointer: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.agent_runtime import executor

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = [HumanMessage(content="hello")]
    mock_build.return_value = MagicMock()
    mock_checkpointer.return_value = MagicMock()
    caplog.set_level(logging.DEBUG, logger="app.agent_runtime.executor")

    await executor._prepare_agent(
        _cfg(thread_id="thread-timing"),
        messages_history=[{"role": "user", "content": "hello"}],
    )

    messages = [record.message for record in caplog.records]
    timing = next(message for message in messages if "agent_prepare_timing" in message)
    assert "model_ms" in timing
    assert "tools_ms" in timing
    assert "middleware_ms" in timing
    assert "build_agent_ms" in timing


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_prepare_agent_skips_memory_tools_when_trigger_writes_are_off(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_checkpointer: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime import executor

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = [HumanMessage(content="hello")]
    mock_build.return_value = MagicMock()
    mock_checkpointer.return_value = MagicMock()
    mock_memory_tool = MagicMock()
    mock_memory_tool.name = "save_user_memory"
    mock_build_memory_tools = MagicMock(return_value=[mock_memory_tool])

    monkeypatch.setattr(executor, "build_memory_tools", mock_build_memory_tools)
    monkeypatch.setattr(executor, "_load_memory_prompt", AsyncMock(return_value=""))
    monkeypatch.setattr(
        executor,
        "_memory_write_policy_for_run",
        AsyncMock(return_value="off"),
        raising=False,
    )

    await executor._prepare_agent(
        _cfg(
            agent_id="00000000-0000-0000-0000-0000000000aa",
            user_id="00000000-0000-0000-0000-000000000001",
            thread_id="00000000-0000-0000-0000-000000000099",
        ),
        messages_history=[{"role": "user", "content": "hello"}],
        is_trigger_mode=True,
    )

    mock_build_memory_tools.assert_not_called()
    build_args = mock_build.call_args.args
    tool_names = {getattr(tool, "name", "") for tool in build_args[1]}
    assert "save_user_memory" not in tool_names
    assert "Long-term Memory Tool Rules" not in build_args[2]


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
async def test_execute_stream_keeps_temporal_context_out_of_user_message(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = [HumanMessage(content="오늘 일정 알려줘")]
    mock_build.return_value = MagicMock()
    captured_stream_messages = {}

    async def fake_stream(_agent, input_, config, **_kwargs):
        captured_stream_messages["messages"] = input_
        captured_stream_messages["config"] = config
        yield "event: message_end\ndata: {}\n\n"

    mock_stream.side_effect = fake_stream

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
    assert system_prompt.startswith("Hello")
    assert "현재 기준 날짜" in system_prompt

    messages = captured_stream_messages["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "오늘 일정 알려줘"


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_execute_stream_forwards_artifact_recorder(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.executor import execute_agent_stream

    recorder = object()
    captured_kwargs: dict[str, object] = {}
    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    mock_checkpointer.return_value = MagicMock()

    async def fake_stream(_agent, _input, _config, **kwargs):
        captured_kwargs.update(kwargs)
        yield "event: message_end\ndata: {}\n\n"

    mock_stream.side_effect = fake_stream

    async for _ in execute_agent_stream(_cfg(), [], artifact_recorder=recorder):
        pass

    assert captured_kwargs["artifact_recorder"] is recorder


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
@patch("app.agent_runtime.executor.FilesystemBackend")
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_execute_stream_injects_skill_tool_dependency(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from app.agent_runtime.executor import execute_agent_stream

    _stub_skill_context(monkeypatch, tmp_path)
    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    captured_configs: list[dict[str, object]] = []
    mock_factory.side_effect = _capture_runtime_tool_configs(captured_configs)

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()
    cfg = _cfg(agent_skills=[_deep_research_skill()])

    async for _ in execute_agent_stream(cfg, []):
        pass

    assert cfg.tools_config == []
    assert captured_configs == [
        {
            "tool_id": "skill-dependency:tavily_search",
            "definition_key": "tavily_search",
            "name": "tavily_search",
            "description": "Hosted Tavily web search used by attached skills.",
            "parameters": {},
            "credential_id": None,
            "credentials": None,
            "user_id": None,
            "agent_id": None,
            "is_skill_dependency": True,
        }
    ]


@pytest.mark.asyncio
@patch("app.agent_runtime.executor.FilesystemBackend")
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_execute_stream_dedupes_exact_skill_dependency_name(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from app.agent_runtime.executor import execute_agent_stream

    _stub_skill_context(monkeypatch, tmp_path)
    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    captured_configs: list[dict[str, object]] = []
    mock_factory.side_effect = _capture_runtime_tool_configs(captured_configs)

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()
    explicit_tool = {
        "tool_id": "tool-1",
        "definition_key": "tavily_search",
        "name": "tavily_search",
        "description": "User-added Tavily",
        "parameters": {},
        "credential_id": None,
        "credentials": None,
    }

    async for _ in execute_agent_stream(
        _cfg(tools_config=[explicit_tool], agent_skills=[_deep_research_skill()]),
        [],
    ):
        pass

    assert captured_configs == [explicit_tool]


@pytest.mark.asyncio
@patch("app.agent_runtime.executor.FilesystemBackend")
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_execute_stream_keeps_stable_dependency_alias_when_explicit_tool_has_different_name(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from app.agent_runtime.executor import execute_agent_stream

    _stub_skill_context(monkeypatch, tmp_path)
    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    captured_configs: list[dict[str, object]] = []
    mock_factory.side_effect = _capture_runtime_tool_configs(captured_configs)

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()
    explicit_tool = {
        "tool_id": "tool-1",
        "definition_key": "tavily_search",
        "name": "Research Search",
        "description": "User-added Tavily under a friendly name",
        "parameters": {},
        "credential_id": None,
        "credentials": None,
    }

    async for _ in execute_agent_stream(
        _cfg(tools_config=[explicit_tool], agent_skills=[_deep_research_skill()]),
        [],
    ):
        pass

    assert [config["name"] for config in captured_configs] == [
        "Research Search",
        "tavily_search",
    ]
    assert captured_configs[1]["definition_key"] == "tavily_search"
    assert captured_configs[1]["is_skill_dependency"] is True


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
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
async def test_execute_stream_passes_recursion_limit_in_config(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = [HumanMessage(content="hi")]
    mock_build.return_value = MagicMock()

    captured_config = {}

    async def capture_stream(agent, messages, config, **_kwargs):
        captured_config.update(config)
        yield "done"

    mock_stream.side_effect = capture_stream

    async for _ in execute_agent_stream(
        _cfg(model_params={"recursion_limit": 77}),
        [{"role": "user", "content": "hi"}],
    ):
        pass

    assert captured_config["recursion_limit"] == 77


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
async def test_execute_stream_injects_product_memory_tools_and_prompt(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
) -> None:
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    with (
        patch(
            "app.agent_runtime.executor._load_memory_prompt",
            new_callable=AsyncMock,
            create=True,
        ) as mock_memory_prompt,
        patch(
            "app.agent_runtime.executor._memory_write_policy_for_run",
            new_callable=AsyncMock,
            create=True,
        ) as mock_memory_write_policy,
    ):
        mock_memory_prompt.return_value = "## Long-term Memory\n- The user prefers Korean."
        mock_memory_write_policy.return_value = "ask"
        async for _ in execute_agent_stream(
            _cfg(
                agent_id="00000000-0000-0000-0000-0000000000aa",
                user_id="00000000-0000-0000-0000-000000000001",
                thread_id="00000000-0000-0000-0000-000000000099",
            ),
            [],
        ):
            pass

    build_args = mock_build.call_args.args
    tool_names = {getattr(tool, "name", "") for tool in build_args[1]}
    assert {"propose_memory", "save_user_memory", "save_agent_memory"} <= tool_names
    assert "## Long-term Memory" in build_args[2]
    assert "explicitly asks you to remember" in build_args[2]
    assert "propose_memory" in build_args[2]
    mock_memory_prompt.assert_awaited_once()
    mock_memory_write_policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_runtime_components_adds_memory_rules_without_memory_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Child subagents get memory tool guidance without unsupported memory args."""
    from app.agent_runtime import executor

    model = MagicMock()
    memory_tool = MagicMock()
    memory_tool.name = "save_user_memory"
    load_memory_prompt = AsyncMock(return_value="SHOULD NOT LOAD")

    monkeypatch.setattr(executor, "_build_model_candidates", lambda _cfg: [model])
    monkeypatch.setattr(executor, "_build_mcp_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(executor, "create_tool_for_runtime", lambda _config: None)
    monkeypatch.setattr(executor, "build_memory_tools", lambda **_kwargs: [memory_tool])
    monkeypatch.setattr(
        executor,
        "_memory_write_policy_for_run",
        AsyncMock(return_value="ask"),
    )
    monkeypatch.setattr(executor, "_load_memory_prompt", load_memory_prompt)

    components = await executor._prepare_runtime_components(
        _cfg(
            agent_id="00000000-0000-0000-0000-0000000000aa",
            user_id="00000000-0000-0000-0000-000000000001",
            thread_id="00000000-0000-0000-0000-000000000099",
        ),
        is_trigger_mode=False,
        include_ask_user=False,
        include_agent_memory_file=False,
    )

    assert memory_tool in components.tools
    assert components.memory_sources is None
    assert "Long-term Memory Tool Rules" in components.system_prompt
    load_memory_prompt.assert_not_awaited()


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
