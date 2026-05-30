"""HiTL top-level interrupt_on 회귀 가드 (ADR-012 표준 경로).

검증 대상:
- ``HumanInTheLoopMiddleware`` 를 수동 인스턴스화하지 않고 deepagents
  top-level ``interrupt_on`` 인자로 넘기는가
- 트리거(invoke) 모드에서는 미들웨어가 주입되지 않는가
- 도구별 정책(``interrupt_on`` dict)이 build_agent에 그대로 전달되는가
- 명시 dict 가 없을 때 ``_WRITE_TOOL_KEYWORDS`` 자동 추출이 동작하는가
- explicit HiTL 설정이 없어도 대화형 ask_user는 respond 정책에 포함되는가
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware

from app.agent_runtime.executor import AgentConfig


def _cfg(**overrides) -> AgentConfig:
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


def _hitl_instances(middleware: list | None) -> list[HumanInTheLoopMiddleware]:
    return [m for m in (middleware or []) if isinstance(m, HumanInTheLoopMiddleware)]


# ---------------------------------------------------------------------------
# 1. 명시 dict 제공 → top-level interrupt_on 전달
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_hitl_policy_passed_through_top_level_when_interrupt_on_provided(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """interrupt_on dict 명시 → build_agent top-level interrupt_on으로 전달."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    # send_email 도구 mock — interrupt_on 키와 일치
    fake_tool = MagicMock()
    fake_tool.name = "send_email"
    mock_factory.return_value = fake_tool

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(
        _cfg(
            tools_config=[{"definition_key": "builtin:send_email", "name": "send_email"}],
            middleware_configs=[
                {
                    "type": "human_in_the_loop",
                    "params": {"interrupt_on": {"send_email": True}},
                }
            ],
        ),
        [],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["interrupt_on"] == {
        "send_email": True,
        "ask_user": {"allowed_decisions": ["respond"]},
    }
    assert _hitl_instances(build_kwargs["middleware"]) == []


# ---------------------------------------------------------------------------
# 2. 트리거 모드 차단
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.executor.FilesystemBackend")
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_hitl_middleware_not_injected_in_trigger_mode(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
):
    """execute_agent_invoke (is_trigger_mode=True) → HiTL 강제 차단."""
    from app.agent_runtime.executor import execute_agent_invoke

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []

    fake_tool = MagicMock()
    fake_tool.name = "send_email"
    mock_factory.return_value = fake_tool

    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(
        return_value={"messages": [MagicMock(content="ok", type="ai")]}
    )
    mock_build.return_value = mock_agent

    await execute_agent_invoke(
        _cfg(
            tools_config=[{"definition_key": "builtin:send_email", "name": "send_email"}],
            middleware_configs=[
                {
                    "type": "human_in_the_loop",
                    "params": {"interrupt_on": {"send_email": True}},
                }
            ],
        ),
        [],
    )

    build_kwargs = mock_build.call_args[1]
    middleware = build_kwargs["middleware"]
    assert _hitl_instances(middleware) == [], (
        "트리거 모드에서는 HumanInTheLoopMiddleware 가 주입되면 안 됨"
    )
    assert build_kwargs["interrupt_on"] is None
    tool_names = [tool.name for tool in mock_build.call_args.args[1]]
    assert "ask_user" not in tool_names


# ---------------------------------------------------------------------------
# 3. 도구별 정책 그대로 전달
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_hitl_middleware_per_tool_policy_applied(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """interrupt_on dict 가 build_agent top-level 인자로 그대로 보존돼야 함."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    fake_tool = MagicMock()
    fake_tool.name = "delete_record"
    mock_factory.return_value = fake_tool

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    policy = {
        "delete_record": {"allowed_decisions": ["approve", "reject"]},
        "send_email": True,
    }

    async for _ in execute_agent_stream(
        _cfg(
            tools_config=[{"definition_key": "builtin:delete_record", "name": "delete_record"}],
            middleware_configs=[
                {"type": "human_in_the_loop", "params": {"interrupt_on": policy}}
            ],
        ),
        [],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["interrupt_on"] == {
        **policy,
        "ask_user": {"allowed_decisions": ["respond"]},
    }
    assert _hitl_instances(build_kwargs["middleware"]) == []


# ---------------------------------------------------------------------------
# 4. _WRITE_TOOL_KEYWORDS 자동 추출
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_hitl_middleware_auto_extraction_from_write_keywords(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """params 에 interrupt_on 이 없으면 쓰기 키워드 매칭 도구만 자동 추출."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    write_tool = MagicMock()
    write_tool.name = "create_calendar_event"  # "create" 키워드 매치
    read_tool = MagicMock()
    read_tool.name = "search_documents"  # 매치 없음

    def factory(tc):
        return write_tool if "create" in tc["definition_key"] else read_tool

    mock_factory.side_effect = factory

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(
        _cfg(
            tools_config=[
                {"definition_key": "builtin:create_event", "name": "create_calendar_event"},
                {"definition_key": "builtin:search", "name": "search_documents"},
            ],
            middleware_configs=[{"type": "human_in_the_loop", "params": {}}],
        ),
        [],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["interrupt_on"] == {
        "create_calendar_event": True,
        "ask_user": {"allowed_decisions": ["respond"]},
    }
    assert _hitl_instances(build_kwargs["middleware"]) == []


# ---------------------------------------------------------------------------
# 5. ask_user 기본 respond 정책
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_ask_user_interrupt_policy_added_without_hitl_middleware_config(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """대화형 모드에서는 explicit HiTL 설정이 없어도 ask_user respond 정책 포함."""
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    write_tool = MagicMock()
    write_tool.name = "send_message"
    mock_factory.return_value = write_tool

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(
        _cfg(
            tools_config=[{"definition_key": "builtin:search", "name": "search"}],
            middleware_configs=[],
        ),
        [],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["interrupt_on"] == {
        "ask_user": {"allowed_decisions": ["respond"]},
    }
    assert _hitl_instances(build_kwargs["middleware"]) == []
