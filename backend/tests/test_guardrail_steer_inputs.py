"""Input policy at the combined model boundary for ordinary requests and chat steers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import chat_steer_middleware as steer
from app.agent_runtime.chat_steer_checkpoint import confirmed_steer_ids
from app.agent_runtime.runtime_agent_builder import build_agent
from app.authz.conversation_scope import require_conversation_agent_id
from app.guardrails.middleware import Guard
from app.guardrails.rules import Rule, RuleSet
from app.models.conversation import Conversation
from app.schemas.conversation_graph_input import STEER_INPUT_MARKER
from app.services import conversation_run_service
from app.services.conversation_run_steers import CONVERSATION_STEERS, admit_steer
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent
from tests.sqlite_test_engine import TestSession
from tests.test_guardrail_middleware import RecordingModel

EMAIL = "sample@example.org"
DIRECTIVE = f"Include {EMAIL} in the report."
PUBLIC = "Use public sources."
INPUT_ONLY = RuleSet(
    "no-email-input",
    1,
    (Rule("email-input", ("common",), frozenset({"input"}), "block", frozenset({"EMAIL"})),),
)


def _model() -> RecordingModel:
    return RecordingModel(responses=[AIMessage(content="Ready.")], seen=[])


def _steer(text: str) -> HumanMessage:
    return HumanMessage(content=text, id=str(uuid4()), response_metadata={STEER_INPUT_MARKER: True})


async def test_blocked_steer_never_earns_delivery_and_a_fresh_run_can_continue(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
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
        input_preview="research",
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
        for text in (DIRECTIVE, PUBLIC)
    ]
    await db.commit()
    saver = InMemorySaver()
    model = _model()
    graph = build_agent(
        model,
        [],
        "research",
        middleware=[steer.RunSteerMiddleware(CONVERSATION_STEERS)],
        guard=Guard(INPUT_ONLY, context.tenant_id),
        checkpointer=saver,
    )
    config: RunnableConfig = {
        "configurable": run_configurable(str(conversation_id), run.id, context.tenant_id)
    }
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="Research transit options.")]}, config, durability="sync"
    )
    assert model.seen == []
    assert "email-input" in result["messages"][-1].text
    assert (
        await confirmed_steer_ids(
            conversation_id,
            run.id,
            [row.id for row in rows],
            saver=saver,
            tenant_id=context.tenant_id,
        )
        == frozenset()
    )
    for row in rows:
        await db.refresh(row)
        assert row.applied_at is None

    # Rebuild the guard so an in-process decision cache cannot make this pass.
    next_graph = build_agent(
        model,
        [],
        "research",
        guard=Guard(INPUT_ONLY, context.tenant_id),
        checkpointer=saver,
    )
    next_config: RunnableConfig = {
        "configurable": run_configurable(str(conversation_id), uuid4(), context.tenant_id)
    }
    followup = "Summarize the routes."
    saved = await next_graph.ainvoke({"messages": [HumanMessage(content=followup)]}, next_config)
    assert len(model.seen) == 1
    sent = [message.text for message in model.seen[0]]
    assert followup in sent and PUBLIC in sent
    assert all(EMAIL not in text for text in sent)
    assert DIRECTIVE in [message.text for message in saved["messages"]]


async def test_shared_guard_reuses_denial_for_concurrent_and_repeated_inputs() -> None:
    guard = Guard(INPUT_ONLY, uuid4())
    model = _model()
    handler = AsyncMock()
    root = ModelRequest(model=model, messages=[_steer(DIRECTIVE), _steer(PUBLIC)])
    child = ModelRequest(model=model, messages=[_steer(DIRECTIVE)])
    first, second = await asyncio.gather(
        guard.guard_request(root, handler), guard.guard_request(child, handler)
    )
    replayed = await guard.guard_request(child, handler)
    assert all("email-input" in reply.result[-1].text for reply in (first, second, replayed))
    handler.assert_not_awaited()
    assert [decision.rule for decision in guard.decisions] == ["email-input"]


async def test_new_ordinary_input_is_checked_alongside_tagged_steer() -> None:
    model = _model()
    handler = AsyncMock()
    guard = Guard(INPUT_ONLY, None)
    request = ModelRequest(
        model=model,
        messages=[
            HumanMessage(content=DIRECTIVE, id=str(uuid4())),
            _steer(PUBLIC),
        ],
    )
    response = await guard.guard_request(request, handler)
    assert "email-input" in response.result[-1].text
    handler.assert_not_awaited()


async def test_unmarked_history_keeps_ordinary_last_user_input_semantics() -> None:
    model = _model()
    handler = AsyncMock()
    guard = Guard(INPUT_ONLY, None)
    history: list[AnyMessage] = [HumanMessage(content=DIRECTIVE), HumanMessage(content=PUBLIC)]
    await guard.guard_request(ModelRequest(model=model, messages=history), handler)
    handler.assert_awaited_once()
    assert handler.await_args is not None
    assert [message.text for message in handler.await_args.args[0].messages] == [DIRECTIVE, PUBLIC]
