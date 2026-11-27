"""Ordinary chat tool execution across a loss of worker ownership."""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from langchain.tools import ToolRuntime
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import recovery_tool_middleware, tool_factory
from app.agent_runtime.recovery_tool_middleware import RecoveryToolMiddleware
from app.agent_runtime.run_error_codes import run_error_code
from app.agent_runtime.run_secrets import reset_run_secrets, set_run_secrets
from app.agent_runtime.runtime_component_builder import build_agent
from app.agent_runtime.tool_factory import create_builtin_tool
from app.chat_recovery.receipts import ToolDispatch, ToolRecoveryBlocked, blocking_receipt_code
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, ConversationRunToolReceipt
from app.services.conversation_run_fence import RunFence, RunOwnershipLost, claim_run_owner
from tests.authz.runtime_prototype import ScriptedModel
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent
from tests.sqlite_test_engine import TestSession

CALL_ID = "tool-call-7"
type Takeover = Callable[[int], Awaitable[RunFence]]


@pytest.fixture
async def claim(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Takeover:
    conversation = await db.get(Conversation, await seed_conversation_with_agent())
    assert conversation is not None
    tenant = unit_context(TEST_USER_ID)
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=tenant.user_id,
        org_id=tenant.org_id,
        tenant_id=tenant.tenant_id,
        source="chat",
        is_active=True,
        status="running",
    )
    db.add(run)
    await db.flush()

    async def own(generation: int) -> RunFence:
        fence = claim_run_owner(run, f"worker-{generation}")
        await db.commit()
        assert fence.generation == generation
        monkeypatch.setattr(recovery_tool_middleware, "current_run_fence", lambda: fence)
        return fence

    return own


def builtin(name: str) -> BaseTool:
    tool = create_builtin_tool(f"builtin:{name}")
    assert tool is not None
    return tool


def request(tool: BaseTool, **args: str) -> ToolCallRequest:
    call = ToolCall(id=CALL_ID, name=tool.name, args=args, type="tool_call")
    state = {"messages": [AIMessage(id="assistant-1", content="", tool_calls=[call])]}
    runtime = ToolRuntime(
        state=state,
        context=None,
        config={},
        stream_writer=lambda _chunk: None,
        tool_call_id=CALL_ID,
        store=None,
    )
    return ToolCallRequest(tool_call=call, tool=tool, state=state, runtime=runtime)


def answer(req: ToolCallRequest, text: str) -> ToolMessage:
    return ToolMessage(
        id="result-1", tool_call_id=CALL_ID, name=req.tool_call["name"], content=text
    )


async def test_completed_read_reuses_first_result_after_takeover(claim: Takeover) -> None:
    await claim(1)
    first = request(builtin("current_datetime"))
    handler = AsyncMock(return_value=answer(first, "2026-11-27T09:00:00+09:00"))
    middleware = RecoveryToolMiddleware()
    result = await middleware.awrap_tool_call(first, handler)

    await claim(2)
    restored = await middleware.awrap_tool_call(request(builtin("current_datetime")), handler)
    assert isinstance(result, ToolMessage) and isinstance(restored, ToolMessage)
    assert (restored.tool_call_id, restored.name, restored.content) == (
        result.tool_call_id,
        result.name,
        result.content,
    )
    handler.assert_awaited_once()


async def test_read_in_progress_can_only_be_retried_by_successor(claim: Takeover) -> None:
    await claim(1)
    req = request(builtin("resolve_relative_date"), expression="today")
    lost = AsyncMock(side_effect=ConnectionError("lost before result persistence"))
    middleware = RecoveryToolMiddleware()
    with pytest.raises(ConnectionError):
        await middleware.awrap_tool_call(req, lost)
    successor = AsyncMock(return_value=answer(req, "2026-11-27"))
    with pytest.raises(ToolRecoveryBlocked) as duplicate:
        await middleware.awrap_tool_call(req, successor)
    assert duplicate.value.code == "tool_call_in_progress"
    successor.assert_not_awaited()

    await claim(2)
    restored = await middleware.awrap_tool_call(req, successor)
    assert isinstance(restored, ToolMessage) and restored.content == "2026-11-27"
    successor.assert_awaited_once()


async def test_old_worker_cannot_confirm_a_result_after_takeover(
    db: AsyncSession, claim: Takeover
) -> None:
    old = await claim(1)
    req = request(builtin("current_datetime"))
    middleware = RecoveryToolMiddleware()

    async def lose_worker(_req: ToolCallRequest) -> ToolMessage:
        await claim(2)
        return answer(req, "late result from previous owner")

    with pytest.raises(RunOwnershipLost):
        await middleware.awrap_tool_call(req, lose_worker)
    row = await db.get(ConversationRunToolReceipt, (old.run_id, CALL_ID))
    assert row is not None and row.status == "started" and row.result_json is None

    successor = AsyncMock(return_value=answer(req, "current worker result"))
    result = await middleware.awrap_tool_call(req, successor)
    assert isinstance(result, ToolMessage) and result.content == "current worker result"
    successor.assert_awaited_once()


async def test_old_worker_cannot_dispatch_an_external_call_after_committing_intent(
    claim: Takeover, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def deliver() -> str:
        """Deliver a notification."""
        return "accepted"

    await claim(1)
    req = request(StructuredTool.from_function(coroutine=deliver))
    reserve = recovery_tool_middleware.reserve_dispatch

    async def reserve_then_lose_owner(fence: RunFence, call: ToolDispatch) -> dict[str, Any] | None:
        cached = await reserve(fence, call)
        await claim(2)
        return cached

    monkeypatch.setattr(recovery_tool_middleware, "reserve_dispatch", reserve_then_lose_owner)
    deliver_to_provider = AsyncMock(return_value=answer(req, "accepted"))
    with pytest.raises(RunOwnershipLost):
        await RecoveryToolMiddleware().awrap_tool_call(req, deliver_to_provider)
    deliver_to_provider.assert_not_awaited()


async def test_unacknowledged_external_write_is_never_sent_twice(claim: Takeover) -> None:
    # The tool's user-selected name matches a trusted builtin; the implementation does not.
    async def external_send() -> str:
        """Send the request to the external destination."""
        return "sent"

    await claim(1)
    req = request(StructuredTool.from_function(coroutine=external_send, name="current_datetime"))
    sent: list[str] = []

    async def send_then_disconnect(_req: ToolCallRequest) -> ToolMessage:
        sent.append(CALL_ID)
        raise ConnectionError("destination accepted before worker stopped")

    middleware = RecoveryToolMiddleware()
    with pytest.raises(ToolRecoveryBlocked) as lost:
        await middleware.awrap_tool_call(req, send_then_disconnect)
    assert lost.value.code == "recovery_effect_unconfirmed"
    await claim(2)
    with pytest.raises(ToolRecoveryBlocked) as unknown:
        await middleware.awrap_tool_call(req, send_then_disconnect)
    assert unknown.value.code == "recovery_effect_unconfirmed"
    assert sent == [CALL_ID]


async def test_external_failure_message_keeps_effect_unconfirmed(
    db: AsyncSession, claim: Takeover
) -> None:
    async def deliver() -> str:
        """Deliver a notification."""
        return "accepted"

    first = await claim(1)
    req = request(StructuredTool.from_function(coroutine=deliver))
    failed = ToolMessage(tool_call_id=CALL_ID, content="connection lost", status="error")
    handler = AsyncMock(return_value=failed)
    middleware = RecoveryToolMiddleware()
    with pytest.raises(ToolRecoveryBlocked) as error:
        await middleware.awrap_tool_call(req, handler)
    assert error.value.code == "recovery_effect_unconfirmed"
    assert (
        await blocking_receipt_code(db, first.run_id, completed_call_ids={CALL_ID})
        == "recovery_effect_unconfirmed"
    )
    await claim(2)
    with pytest.raises(ToolRecoveryBlocked) as unknown:
        await middleware.awrap_tool_call(req, handler)
    assert unknown.value.code == "recovery_effect_unconfirmed"
    handler.assert_awaited_once()


async def test_recovery_does_not_dispatch_a_new_external_write(claim: Takeover) -> None:
    async def deliver() -> str:
        """Deliver a notification."""
        return "accepted"

    await claim(1)
    await claim(2)
    req = request(StructuredTool.from_function(coroutine=deliver))
    handler = AsyncMock(return_value=answer(req, "accepted"))
    with pytest.raises(ToolRecoveryBlocked) as blocked:
        await RecoveryToolMiddleware().awrap_tool_call(req, handler)
    assert blocked.value.code == "recovery_tool_not_allowed"
    handler.assert_not_awaited()


@pytest.mark.parametrize("change", ["args", "implementation"])
async def test_completed_result_is_bound_to_original_request(claim: Takeover, change: str) -> None:
    await claim(1)
    tool = builtin("resolve_relative_date")
    req = request(tool, expression="today")
    middleware = RecoveryToolMiddleware()
    handler = AsyncMock(return_value=answer(req, "2026-11-27"))
    await middleware.awrap_tool_call(req, handler)
    await claim(2)

    if change == "args":
        changed = request(tool, expression="tomorrow")
    else:

        async def replacement(expression: str) -> str:
            """Custom implementation with the same advertised name and arguments."""
            return expression

        changed = request(
            StructuredTool.from_function(coroutine=replacement, name=tool.name), expression="today"
        )
    with pytest.raises(ToolRecoveryBlocked) as mismatch:
        await middleware.awrap_tool_call(changed, handler)
    assert mismatch.value.code == "tool_receipt_conflict"
    handler.assert_awaited_once()


async def test_sensitive_result_never_enters_replay_record(
    db: AsyncSession, claim: Takeover
) -> None:
    fence = await claim(1)
    req = request(builtin("current_datetime"))
    secret = "fixture-receipt-sensitive-value"
    handler = AsyncMock(return_value=answer(req, f"value={secret}"))
    middleware = RecoveryToolMiddleware()
    token = set_run_secrets({secret})
    try:
        await middleware.awrap_tool_call(req, handler)
    finally:
        reset_run_secrets(token)
    row = await db.get(ConversationRunToolReceipt, (fence.run_id, CALL_ID))
    assert row is not None and row.status == "completed" and row.result_json is None

    await claim(2)
    with pytest.raises(ToolRecoveryBlocked) as unavailable:
        await middleware.awrap_tool_call(req, handler)
    assert unavailable.value.code == "tool_result_unavailable"
    handler.assert_awaited_once()


async def test_ordinary_graph_records_and_replays_the_real_builtin(
    db: AsyncSession, claim: Takeover, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Mock(wraps=tool_factory.build_temporal_context)
    monkeypatch.setattr(tool_factory, "build_temporal_context", clock)
    call = ToolCall(name="current_datetime", args={}, id=CALL_ID, type="tool_call")

    fence = await claim(1)
    observed: list[tuple[str | None, object, object]] = []

    class ObserveToolCompletion(AsyncCallbackHandler):
        async def on_tool_end(self, output: Any, **kwargs: Any) -> None:
            async with TestSession() as reader:
                row = await reader.get(ConversationRunToolReceipt, (fence.run_id, CALL_ID))
                saved = row.result_json if row else None
                observed.append(
                    (row.status if row else None, saved and saved.get("content"), output.content)
                )

    async def run_graph() -> ToolMessage:
        model = ScriptedModel(
            responses=[
                AIMessage(id="clock-request", content="", tool_calls=[call]),
                AIMessage(content="finished"),
            ]
        )
        graph = build_agent(model, [builtin("current_datetime")], "Report the time")
        result = await graph.ainvoke(
            {"messages": [("user", "What time is it?")]},
            config={"callbacks": [ObserveToolCompletion()]},
        )
        assert result["messages"][-1].content == "finished"
        messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(messages) == 1
        return messages[0]

    first = await run_graph()
    assert observed == [("completed", first.content, first.content)]
    assert clock.call_count == 1
    receipt = await db.get(ConversationRunToolReceipt, (fence.run_id, CALL_ID))
    assert receipt is not None and receipt.status == "completed"
    assert receipt.result_json is not None and receipt.result_json["content"] == first.content

    await claim(2)
    replayed = await run_graph()
    assert replayed.tool_call_id == CALL_ID and replayed.content == first.content
    assert all(saved == shown == replayed.content for _, saved, shown in observed)
    assert clock.call_count == 1


async def test_confirmed_write_requires_its_own_checkpointed_answer(
    db: AsyncSession, claim: Takeover
) -> None:
    async def deliver() -> str:
        """Deliver a notification."""
        return "accepted"

    owner = await claim(1)
    req = request(StructuredTool.from_function(coroutine=deliver))
    receipt = answer(req, "accepted")
    handler = AsyncMock(return_value=receipt)
    assert await RecoveryToolMiddleware().awrap_tool_call(req, handler) == receipt
    assert (
        await blocking_receipt_code(db, owner.run_id, completed_call_ids={"different-call"})
        == "recovery_effect_unconfirmed"
    )
    assert await blocking_receipt_code(db, owner.run_id, completed_call_ids={CALL_ID}) is None
    handler.assert_awaited_once()


@pytest.mark.parametrize("code", ["recovery_effect_unconfirmed", "tool_receipt_conflict"])
def test_stream_failure_preserves_the_underlying_recovery_code(code: str) -> None:
    wrapped = RuntimeError("stream worker stopped")
    wrapped.__cause__ = ToolRecoveryBlocked(code)
    assert run_error_code(wrapped, "stream_error") == code


async def test_unconfirmed_delivery_stops_graph_before_a_fresh_model_tool_call(
    claim: Takeover,
) -> None:
    dispatched: list[str] = []

    async def deliver(destination: str) -> str:
        """Deliver once before losing the provider's acknowledgement."""
        dispatched.append(destination)
        raise ConnectionError("provider acknowledgement lost")

    await claim(1)
    first = ToolCall(name="deliver", id=CALL_ID, args={"destination": "inbox"}, type="tool_call")
    next_call = ToolCall(
        name="deliver", id="second-call", args={"destination": "inbox"}, type="tool_call"
    )
    model = ScriptedModel(
        responses=[
            AIMessage(content="", tool_calls=[first]),
            AIMessage(content="", tool_calls=[next_call]),
            AIMessage(content="finished"),
        ]
    )
    graph = build_agent(model, [StructuredTool.from_function(coroutine=deliver)], "Deliver")
    with pytest.raises(ToolRecoveryBlocked) as blocked:
        await graph.ainvoke({"messages": [("user", "Deliver to my inbox")]})
    assert blocked.value.code == "recovery_effect_unconfirmed"
    assert dispatched == ["inbox"]


async def test_external_confirmation_write_failure_stops_execution(
    claim: Takeover, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliveries: list[str] = []

    async def deliver() -> str:
        """Send an external notification."""
        deliveries.append(CALL_ID)
        return "accepted"

    await claim(1)
    req = request(StructuredTool.from_function(coroutine=deliver))
    confirm = AsyncMock(side_effect=ConnectionError("journal commit lost"))
    monkeypatch.setattr(recovery_tool_middleware, "confirm_dispatch", confirm)

    async def invoke(_req: ToolCallRequest) -> ToolMessage:
        return answer(req, await deliver())

    with pytest.raises(ToolRecoveryBlocked) as blocked:
        await RecoveryToolMiddleware().awrap_tool_call(req, invoke)
    assert blocked.value.code == "recovery_effect_unconfirmed"
    assert deliveries == [CALL_ID]
    confirm.assert_awaited_once()
