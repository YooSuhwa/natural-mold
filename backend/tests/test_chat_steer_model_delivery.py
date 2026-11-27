"""A checkpoint that stages a steer is insufficient proof of model delivery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import chat_steer_middleware as steer
from app.agent_runtime.chat_steer_checkpoint import confirmed_steer_ids
from app.authz.conversation_scope import require_conversation_agent_id
from app.models.conversation import Conversation
from app.services import conversation_run_service, conversation_run_steer_rollover
from app.services.conversation_run_steers import admit_steer
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent
from tests.sqlite_test_engine import TestSession

DeliveryCase = Literal["interrupt_before_model", "synthetic", "drop", "rewrite"]


@pytest.mark.parametrize("behavior", ["interrupt_before_model", "synthetic", "drop", "rewrite"])
async def test_only_steers_seen_by_the_model_get_durable_delivery_receipts(
    behavior: DeliveryCase, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steer, "tenant_session", lambda _: TestSession())
    context = unit_context(TEST_USER_ID)
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conversation),
        context=context,
        source="chat",
        input_preview="compare suppliers",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    rows = [
        await admit_steer(
            db,
            conversation_id=conversation_id,
            run_id=run.id,
            context=context,
            client_request_id=uuid4(),
            text=text,
        )
        for text in ("use Korean sources", "prioritize public suppliers")
    ]
    await db.commit()

    observed_inputs: list[list[str]] = []
    wrapper_calls = []

    class RecordingModel(FakeMessagesListChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> RecordingModel:
            return self

        def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
            observed_inputs.append([m.text for m in messages if isinstance(m, HumanMessage)])
            return super()._generate(messages, *args, **kwargs)

    class DownstreamMiddleware(AgentMiddleware[steer.RunSteerState]):
        state_schema = steer.RunSteerState

        async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        ) -> ModelResponse | AIMessage:
            wrapper_calls.append(behavior)
            if behavior == "synthetic":
                return AIMessage(content="cached result")
            # A wrapper may preserve the same message ID but alter its content.
            # The other admitted steer must still earn its own receipt.
            modified: list[AnyMessage] = []
            for message in request.messages:
                if message.id != steer.steer_message_id(rows[0].id):
                    modified.append(message)
                elif behavior == "rewrite":
                    modified.append(HumanMessage(id=message.id, content="a different instruction"))
            return await handler(request.override(messages=modified))

    saver = InMemorySaver()
    graph = create_deep_agent(
        model=RecordingModel(responses=[AIMessage(content="answer")]),
        middleware=[steer.RunSteerMiddleware(), DownstreamMiddleware()],
        checkpointer=saver,
    )
    config: RunnableConfig = {
        "configurable": run_configurable(str(conversation_id), run.id, context.tenant_id)
    }
    await graph.ainvoke(
        {"messages": [HumanMessage(content="compare suppliers")]},
        config,
        durability="sync",
        interrupt_before=["model"] if behavior == "interrupt_before_model" else None,
    )
    if behavior == "interrupt_before_model":
        assert (await graph.aget_state(config)).next == ("model",)
        assert wrapper_calls == []
    else:
        assert wrapper_calls == [behavior]
    if behavior == "drop":
        assert observed_inputs == [["compare suppliers", rows[1].text]]
    elif behavior == "rewrite":
        assert observed_inputs == [["compare suppliers", "a different instruction", rows[1].text]]
    else:
        assert observed_inputs == []

    expected = {rows[1].id} if observed_inputs else set()
    proof = await confirmed_steer_ids(
        conversation_id, run.id, [row.id for row in rows], saver=saver, tenant_id=context.tenant_id
    )
    assert proof == expected

    # Recover as a new worker after the checkpoint was already persisted. Any
    # attempted instruction absent from the actual call stays ambiguous.
    monkeypatch.setattr(
        conversation_run_steer_rollover, "conversation_checkpointer", AsyncMock(return_value=saver)
    )
    await conversation_run_service.transition_run(db, run, "failed")
    await conversation_run_steer_rollover.reconcile_terminal_steers(
        db, conversation_id=conversation_id, run_id=run.id, context=context
    )
    await db.commit()
    for row in rows:
        await db.refresh(row)
        assert row.delivery_started_at is not None
        assert row.status == ("applied" if row.id in expected else "needs_review")
        assert (row.applied_at is not None) == (row.id in expected)
        assert row.queued_input_id is None
