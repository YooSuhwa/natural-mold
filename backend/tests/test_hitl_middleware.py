"""HiTL middleware 명시 인스턴스화 회귀 가드 (ADR-012 Phase 1).

검증 대상:
- ``HumanInTheLoopMiddleware`` 가 명시적으로 인스턴스화되어 deep agent 의
  미들웨어 list 에 들어가는가
- 트리거(invoke) 모드에서는 미들웨어가 주입되지 않는가
- 도구별 정책(``interrupt_on`` dict)이 인스턴스에 그대로 전달되는가
- 명시 dict 가 없을 때 ``_WRITE_TOOL_KEYWORDS`` 자동 추출이 동작하는가
- ``create_deep_agent(interrupt_on=...)`` 자동 주입은 비활성(``None``)인가
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
# 1. 명시 dict 제공 → 인스턴스 주입
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_hitl_middleware_instance_injected_when_interrupt_on_provided(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """interrupt_on dict 명시 → HumanInTheLoopMiddleware 인스턴스가 list 에 추가."""
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
    middleware = build_kwargs["middleware"]
    hitl = _hitl_instances(middleware)
    assert len(hitl) == 1, "HumanInTheLoopMiddleware 인스턴스가 정확히 하나 주입되어야 함"


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
    # build_agent 의 interrupt_on 인자도 None — deepagents 자동 주입 차단
    assert build_kwargs["interrupt_on"] is None


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
    """interrupt_on dict 가 미들웨어 인스턴스에 그대로 보존돼야 함."""
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
    hitl = _hitl_instances(build_kwargs["middleware"])
    assert len(hitl) == 1
    # HumanInTheLoopMiddleware 는 ``interrupt_on`` 을 ``tool_configs`` 속성에
    # 보관 (langchain.agents.middleware.human_in_the_loop 구현). 키 보존만 검증.
    instance = hitl[0]
    stored = getattr(instance, "tool_configs", None) or getattr(instance, "interrupt_on", None)
    assert stored is not None
    assert set(stored.keys()) == {"delete_record", "send_email"}


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
    hitl = _hitl_instances(build_kwargs["middleware"])
    assert len(hitl) == 1, "쓰기 도구가 있으면 HumanInTheLoopMiddleware 가 주입돼야 함"
    instance = hitl[0]
    stored = getattr(instance, "tool_configs", None) or getattr(instance, "interrupt_on", None)
    assert stored is not None
    assert "create_calendar_event" in stored
    assert "search_documents" not in stored


# ---------------------------------------------------------------------------
# 5. deepagents 자동 주입 회피 (interrupt_on=None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_deepagents_interrupt_on_param_is_none_when_explicit_instance(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """명시 인스턴스 경로 사용 시 build_agent 의 interrupt_on 인자는 항상 None.

    이로써 ``create_deep_agent`` 의 자동 ``HumanInTheLoopMiddleware`` 주입이
    비활성화돼 미들웨어 중복 등록을 방지한다.
    """
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
            tools_config=[{"definition_key": "builtin:send_message", "name": "send_message"}],
            middleware_configs=[
                {
                    "type": "human_in_the_loop",
                    "params": {"interrupt_on": {"send_message": True}},
                }
            ],
        ),
        [],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    # 명시 인스턴스가 미들웨어 list 에 있고, build_agent 의 interrupt_on 인자는 None
    assert len(_hitl_instances(build_kwargs["middleware"])) == 1
    assert build_kwargs["interrupt_on"] is None
