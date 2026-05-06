"""HiTL Phase 4 — ask_user retire 회귀 가드 (베조스 M5 DRI).

ADR-012 §Phase 4 (옵션 B) — 메인 채팅 ``ask_user`` 도구 retire 후의
**행동 잠금(lock) 가드**. 본 파일은 향후 누군가 ``ask_user`` 를 메인
채팅 경로에 다시 끼워 넣거나, builder_v3 native interrupt 어댑터를
부주의하게 망가뜨리는 것을 차단한다.

가드 범위:

1. **메인 채팅 ask_user 도구 미주입** — ``execute_agent_stream`` /
   ``execute_agent_invoke`` 가 ``build_agent`` 에 넘기는 도구 list 에
   ``ask_user`` 가 절대 포함되지 않는다 (사용자가 명시 user_tool 로
   넣지 않는 한). Phase 3 까지의 자동 주입 회귀 가드.

2. **모듈에서 ask_user 심볼 import 불가** — ``app.agent_runtime.tools``
   서브모듈에서 ``ask_user`` 가 더는 import 되지 않으며,
   ``executor`` 도 ``ask_user_tool`` 심볼을 노출하지 않음.

3. **트리거 모드 interrupt_on 강제 None 보존** — ``is_trigger_mode=True``
   경로에서 ``interrupt_on`` 이 항상 None 으로 강제되어 트리거 환경의
   HiTL hang 회귀 차단 (M3 jensen 패턴: include_ask_user 책임 분리).

4. **builder_v3 native interrupt → streaming 어댑터 회귀 가드** —
   ``_interrupt_to_standard_chunk`` 가 ``{"type":"ask_user"...}`` 를
   계속 표준 ``respond`` 액션 chunk 로 어댑트한다 (옵션 / 옵션 없음).
   본 어댑터는 builder_v3 (``builder_v3/nodes/phase2_intent.py`` +
   ``router.py``) 가 발행하는 native interrupt 의 frontend 호환성을
   책임지므로 Phase 4 retire 후에도 보존 필수.

회귀 시그널: 본 파일의 한 건이라도 실패하면 Phase 4 retire 의 가정이
깨진 것 — 즉시 의심하고 production diff 를 재검토할 것.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_runtime.executor import AgentConfig
from app.agent_runtime.streaming import _interrupt_to_standard_chunk


def _cfg(**overrides) -> AgentConfig:
    defaults: dict[str, object] = dict(
        provider="openai",
        model_name="gpt-4o",
        api_key="sk-test",
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        thread_id="t-phase4-retire",
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. 모듈 import 잔재 — ask_user 심볼이 retire 됐는지
# ---------------------------------------------------------------------------


def test_ask_user_module_is_retired() -> None:
    """``app.agent_runtime.tools.ask_user`` 모듈 자체가 더는 존재하지 않는다.

    누군가 다시 추가하면 본 테스트가 실패해 의도 환기.
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.agent_runtime.tools.ask_user")


def test_executor_does_not_expose_ask_user_tool_symbol() -> None:
    """``executor`` 모듈에서 ``ask_user_tool`` 심볼이 더는 export 되지 않는다."""
    from app.agent_runtime import executor

    assert not hasattr(executor, "ask_user_tool"), (
        "executor.ask_user_tool 은 Phase 4 에서 제거됨 — 다시 추가되면 회귀."
    )


# ---------------------------------------------------------------------------
# 2. 메인 채팅 stream — ask_user 도구 자동 주입 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.stream_agent_response")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_main_chat_stream_never_injects_ask_user(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
) -> None:
    """``execute_agent_stream`` 이 빌드한 도구 list 에 ``ask_user`` 가 없다.

    user_tools 가 비어 있으면 도구 list 도 비어야 하며 (자동 주입 X),
    user_tools 가 있어도 ``ask_user`` 라는 이름의 도구는 포함되지 않는다.
    """
    from app.agent_runtime.executor import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    user_tool = MagicMock()
    user_tool.name = "send_email"
    mock_factory.return_value = user_tool

    async def fake_stream(*args, **kwargs):
        yield "event: message_end\ndata: {}\n\n"

    mock_stream.return_value = fake_stream()

    # Case A: tools_config 비어있음 → 도구 0개 (ask_user 자동 주입 X)
    async for _ in execute_agent_stream(_cfg(tools_config=[]), []):
        pass

    tools_passed_a: list = mock_build.call_args[0][1]
    assert len(tools_passed_a) == 0, "tools_config 빈 경우 도구 0개 — ask_user 자동 주입 회귀"
    assert not any(getattr(t, "name", None) == "ask_user" for t in tools_passed_a)

    mock_build.reset_mock()

    # Case B: user_tool 한 개 → 그 도구만, ask_user 자동 추가 없음
    async for _ in execute_agent_stream(
        _cfg(tools_config=[{"definition_key": "builtin:send_email", "name": "send_email"}]),
        [],
    ):
        pass

    tools_passed_b: list = mock_build.call_args[0][1]
    names_b = [getattr(t, "name", None) for t in tools_passed_b]
    assert "ask_user" not in names_b, (
        "사용자 도구만 있을 때 ask_user 가 자동 주입되면 Phase 4 retire 회귀"
    )
    assert names_b == ["send_email"]


@pytest.mark.asyncio
@patch("app.agent_runtime.executor.FilesystemBackend")
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.executor.build_agent")
@patch("app.agent_runtime.executor.convert_to_langchain_messages")
@patch("app.agent_runtime.executor.create_chat_model")
@patch("app.agent_runtime.executor.create_tool_for_runtime")
async def test_trigger_invoke_never_injects_ask_user_and_blocks_hitl(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
) -> None:
    """트리거 (invoke) 경로 — ``ask_user`` 도구 미주입 + ``interrupt_on=None`` 강제.

    M3 (jensen) 의 핵심 결정 보존: ``include_ask_user`` 의 두 책임 중
    (b) 트리거 모드 HiTL 강제 차단 기능을 ``is_trigger_mode`` 로 rename
    하여 보존했다 — 트리거 모드에서 사용자 없이 interrupt 발생하면 hang.
    본 가드는 그 차단이 회귀하지 않음을 검증.
    """
    from app.agent_runtime.executor import execute_agent_invoke

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []

    user_tool = MagicMock()
    user_tool.name = "send_email"
    mock_factory.return_value = user_tool

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
    tools_passed = mock_build.call_args[0][1]
    names = [getattr(t, "name", None) for t in tools_passed]

    assert "ask_user" not in names, "트리거 모드에서 ask_user 자동 주입 시 Phase 4 회귀"
    assert build_kwargs["interrupt_on"] is None, (
        "트리거 모드 deepagents 자동 interrupt_on 인자는 항상 None"
    )


# ---------------------------------------------------------------------------
# 3. builder_v3 native interrupt → streaming 어댑터 (보존 영역)
# ---------------------------------------------------------------------------


def test_builder_v3_native_ask_user_with_options_adapted_to_respond_chunk() -> None:
    """builder_v3 가 ``options`` 와 함께 발행하는 native interrupt 어댑트.

    Phase 4 retire 와 무관하게 본 어댑터는 builder_v3 영역의 native
    interrupt → 표준 wire chunk 변환 책임을 유지해야 한다.
    """
    intr_value = {
        "type": "ask_user",
        "question": "어떤 모델을 쓸까요?",
        "options": ["GPT-4o", "Claude 3.5 Sonnet"],
    }
    chunk = _interrupt_to_standard_chunk("ns-builder-1", intr_value)

    assert chunk is not None
    assert chunk["interrupt_id"] == "ns-builder-1"

    actions = chunk["action_requests"]
    assert len(actions) == 1
    assert actions[0]["name"] == "ask_user"
    assert actions[0]["args"] == {
        "question": "어떤 모델을 쓸까요?",
        "options": ["GPT-4o", "Claude 3.5 Sonnet"],
    }
    assert actions[0]["type"] == "tool_call"

    reviews = chunk["review_configs"]
    assert len(reviews) == 1
    # 어댑터는 ``respond`` 단일 결정만 허용 (사용자 응답 텍스트 채택).
    assert reviews[0]["allowed_decisions"] == ["respond"]
    assert reviews[0]["tool_name"] == "ask_user"


def test_builder_v3_native_ask_user_without_options_adapted_to_respond_chunk() -> None:
    """builder_v3 의 router fallback (``options`` 없음) native interrupt 어댑트.

    ``router.py`` 가 발행하는 fallback ask_user 는 옵션 없이 question
    만 동반한다. 어댑터는 빈 옵션 list 로 정상 변환해야 한다.
    """
    intr_value = {"type": "ask_user", "question": "무엇을 도와드릴까요?"}
    chunk = _interrupt_to_standard_chunk("ns-builder-2", intr_value)

    assert chunk is not None
    assert chunk["interrupt_id"] == "ns-builder-2"

    actions = chunk["action_requests"]
    assert len(actions) == 1
    assert actions[0]["name"] == "ask_user"
    # options 누락 시 빈 list 로 정규화 (frontend UI 호환성).
    assert actions[0]["args"] == {"question": "무엇을 도와드릴까요?", "options": []}

    reviews = chunk["review_configs"]
    assert reviews[0]["allowed_decisions"] == ["respond"]


def test_unknown_native_interrupt_shape_is_skipped_after_retire() -> None:
    """``ask_user`` / 표준 HITLRequest 외 dict 는 None (skip).

    Phase 4 retire 후에도 unknown shape 정책은 동일 — 표준 chunk 도
    legacy chunk 도 emit 하지 않는다.
    """
    assert _interrupt_to_standard_chunk("ns-x", {"type": "unknown"}) is None
    assert _interrupt_to_standard_chunk("ns-x", {"random": "stuff"}) is None
    assert _interrupt_to_standard_chunk("ns-x", None) is None
