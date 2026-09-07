from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import (
    conversation_run_queue_service,
    conversation_run_queue_worker,
    conversation_run_service,
    conversation_run_worker,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


@pytest.mark.asyncio
async def test_interrupt_persists_correction_and_cancellation_in_one_request(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    # Given an active predecessor owned by the conversation user.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    active = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        input_preview="old direction",
    )
    await conversation_run_service.transition_run(
        db,
        active,
        "running",
        worker_instance_id="remote-owner",
    )
    await db.commit()

    # When the client sends a correction with interrupt semantics.
    response = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
        json={
            "id": "steer-command",
            "method": "run.start",
            "params": {
                "client_request_id": "steer-request",
                "multitask_strategy": "interrupt",
                "input": {"messages": [{"role": "user", "content": "use the corrected direction"}]},
            },
        },
    )

    # Then correction remains pending while durable cancellation awaits owner ack.
    assert response.status_code == 200
    assert response.json()["result"]["input_status"] == "pending"
    await db.refresh(active)
    assert active.status == "canceling"
    assert active.cancel_reason == "steer"
    queued = (
        await db.execute(
            select(ConversationRunInput).where(
                ConversationRunInput.client_request_id == "steer-request"
            )
        )
    ).scalar_one()
    assert queued.run_id is None
    assert queued.priority == 100


@pytest.mark.asyncio
async def test_interrupt_api_dispatches_correction_only_after_owner_ack(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a local owning worker that records an effect before blocking.
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        predecessor = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="old direction",
        )
        await db.commit()
        predecessor_id = predecessor.id

    predecessor_entered = anyio.Event()
    predecessor_effect_recorded = anyio.Event()
    correction_completed = anyio.Event()
    correction_payloads: list[dict[str, Any]] = []

    async def predecessor_executor(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        predecessor_effect_recorded.set()
        predecessor_entered.set()
        await anyio.sleep_forever()
        if False:
            yield ""

    async def correction_executor(
        _cfg: Any,
        input_payload: dict[str, Any],
        **_kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        assert predecessor_effect_recorded.is_set()
        correction_payloads.append(input_payload)
        correction_completed.set()
        if False:
            yield ""

    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="api-owner")
    conversation_run_worker.reset_run_task_registry_for_tests(registry)
    monkeypatch.setattr(
        conversation_run_queue_worker,
        "execute_agent_stream_langgraph",
        correction_executor,
    )
    await conversation_run_worker.start_conversation_run(
        run_id=predecessor_id,
        conversation_id=conversation_id,
        cfg=cast(AgentConfig, SimpleNamespace(secret_values=set(), agent_id=None)),
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        input_payload={"messages": [{"role": "user", "content": "old"}]},
        moldy_source="chat",
        executor_fn=predecessor_executor,
        registry=registry,
    )
    predecessor_task = registry.get(predecessor_id)
    assert predecessor_task is not None
    with anyio.fail_after(2):
        await predecessor_entered.wait()

    # When the API accepts an interrupt correction.
    response = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
        json={
            "id": "durable-handoff",
            "method": "run.start",
            "params": {
                "client_request_id": "durable-handoff",
                "multitask_strategy": "interrupt",
                "input": {"messages": [{"role": "user", "content": "corrected"}]},
            },
        },
    )
    assert response.status_code == 200
    input_id = uuid.UUID(response.json()["result"]["input_id"])
    await predecessor_task

    # Then the predecessor acknowledges cancellation before the correction is claimed.
    with anyio.fail_after(3):
        await correction_completed.wait()
    async with TestSession() as db:
        claimed_input = await db.get(ConversationRunInput, input_id)
        assert claimed_input is not None and claimed_input.run_id is not None
        successor_id = claimed_input.run_id
    successor_task = registry.get(successor_id)
    assert successor_task is not None
    await successor_task
    async with TestSession() as db:
        acknowledged = await db.get(ConversationRun, predecessor_id)
        queued = await db.get(ConversationRunInput, input_id)
        assert acknowledged is not None
        assert queued is not None and queued.run_id is not None
        successor = await db.get(ConversationRun, queued.run_id)
    assert acknowledged.status == "canceled"
    assert acknowledged.cancellation_acknowledged_at is not None
    assert queued.claimed_at is not None
    assert queued.claimed_at >= acknowledged.cancellation_acknowledged_at
    assert successor is not None and successor.status == "completed"
    assert correction_payloads == [{"messages": [{"role": "user", "content": "corrected"}]}]


@pytest.mark.asyncio
async def test_enqueue_request_retry_returns_same_durable_input(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an idle conversation and dispatch held before claim.
    conversation_id = await seed_conversation_with_agent()

    async def hold_dispatch(_conversation_id: uuid.UUID):
        return None

    monkeypatch.setattr(
        "app.services.conversation_run_queue_worker.dispatch_next_for_conversation",
        hold_dispatch,
    )
    payload = {
        "id": "enqueue-command",
        "method": "run.start",
        "params": {
            "clientRequestId": "stable-request-id",
            "multitaskStrategy": "enqueue",
            "input": {"messages": [{"role": "user", "content": "durable"}]},
        },
    }

    # When the identical accepted request is retried.
    first = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
        json=payload,
    )
    second = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
        json=payload,
    )

    # Then both responses bind to one persisted input and no run is duplicated.
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["result"]["input_id"] == second.json()["result"]["input_id"]
    rows = list(
        (
            await db.execute(
                select(ConversationRunInput).where(
                    ConversationRunInput.client_request_id == "stable-request-id"
                )
            )
        ).scalars()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_enqueue_does_not_cancel_same_process_active_run(
    client: AsyncClient,
) -> None:
    # Given a healthy active worker registered in this process.
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        active = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="active",
        )
        await db.commit()
        active_id = active.id
    entered = anyio.Event()

    async def blocked_executor(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        entered.set()
        await anyio.sleep_forever()
        if False:
            yield ""

    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="enqueue-owner")
    conversation_run_worker.reset_run_task_registry_for_tests(registry)
    await conversation_run_worker.start_conversation_run(
        run_id=active_id,
        conversation_id=conversation_id,
        cfg=cast(AgentConfig, SimpleNamespace(secret_values=set(), agent_id=None)),
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        input_payload={"messages": [{"role": "user", "content": "active"}]},
        moldy_source="chat",
        executor_fn=blocked_executor,
        registry=registry,
    )
    task = registry.get(active_id)
    assert task is not None
    with anyio.fail_after(2):
        await entered.wait()

    # When an ordinary follow-up is enqueued through the real route.
    response = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
        json={
            "id": "ordinary-enqueue",
            "method": "run.start",
            "params": {
                "client_request_id": "ordinary-enqueue",
                "multitask_strategy": "enqueue",
                "input": {"messages": [{"role": "user", "content": "later"}]},
            },
        },
    )

    # Then no process-local or durable cancellation is issued.
    assert response.status_code == 200
    assert response.json()["result"]["input_status"] == "pending"
    assert not task.done()
    async with TestSession() as db:
        await conversation_run_queue_service.pause_queue(
            db,
            conversation_id=conversation_id,
            user_id=TEST_USER_ID,
        )
        durable_active = await db.get(ConversationRun, active_id, with_for_update=True)
        assert durable_active is not None
        assert durable_active.status == "running"
        assert durable_active.cancel_reason is None
        await conversation_run_service.request_cancel_run(db, durable_active, reason="stop")
        await db.commit()
    registry.request_cancel(active_id, reason="stop")
    await task


@pytest.mark.asyncio
async def test_interrupt_retry_does_not_cancel_its_resulting_run(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an active predecessor and a correction executor that remains running.
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        predecessor = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            input_preview="active",
        )
        await db.commit()
        predecessor_id = predecessor.id
    predecessor_entered = anyio.Event()
    correction_entered = anyio.Event()

    async def predecessor_executor(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        predecessor_entered.set()
        await anyio.sleep_forever()
        if False:
            yield ""

    async def correction_executor(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        correction_entered.set()
        await anyio.sleep_forever()
        if False:
            yield ""

    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="retry-owner")
    conversation_run_worker.reset_run_task_registry_for_tests(registry)
    monkeypatch.setattr(
        conversation_run_queue_worker,
        "execute_agent_stream_langgraph",
        correction_executor,
    )
    await conversation_run_worker.start_conversation_run(
        run_id=predecessor_id,
        conversation_id=conversation_id,
        cfg=cast(AgentConfig, SimpleNamespace(secret_values=set(), agent_id=None)),
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        input_payload={"messages": [{"role": "user", "content": "active"}]},
        moldy_source="chat",
        executor_fn=predecessor_executor,
        registry=registry,
    )
    with anyio.fail_after(2):
        await predecessor_entered.wait()
    payload = {
        "id": "retry-interrupt",
        "method": "run.start",
        "params": {
            "client_request_id": "retry-interrupt",
            "multitask_strategy": "interrupt",
            "input": {"messages": [{"role": "user", "content": "correction"}]},
        },
    }

    # When the accepted interrupt is delivered, starts, and the request is retried.
    first = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
        json=payload,
    )
    with anyio.fail_after(3):
        await correction_entered.wait()
    second = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
        json=payload,
    )

    # Then replay returns the same input without canceling its resulting run.
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["result"]["input_id"] == second.json()["result"]["input_id"]
    active_ids = registry.active_run_ids()
    assert len(active_ids) == 1
    successor_id = next(iter(active_ids))
    successor_task = registry.get(successor_id)
    assert successor_task is not None and not successor_task.done()
    async with TestSession() as db:
        successor = await db.get(ConversationRun, successor_id, with_for_update=True)
        assert successor is not None
        assert successor.status == "running"
        assert successor.cancel_reason is None
        await conversation_run_service.request_cancel_run(db, successor, reason="stop")
        await db.commit()
    registry.request_cancel(successor_id, reason="stop")
    await successor_task


@pytest.mark.asyncio
async def test_queued_fork_is_rejected_before_checkpoint_mutation(client: AsyncClient) -> None:
    # Given a run.start request that combines queueing and branch edit state.
    conversation_id = await seed_conversation_with_agent()

    # When the unsupported combination reaches the protocol boundary.
    response = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
        json={
            "id": "queued-edit",
            "method": "run.start",
            "params": {
                "multitask_strategy": "enqueue",
                "checkpoint": {"checkpoint_id": "ck-old"},
                "input": {"messages": [{"role": "user", "content": "edit"}]},
            },
        },
    )

    # Then it fails explicitly rather than writing concurrent graph state.
    assert response.status_code == 200
    assert response.json()["error"]["code"] == "QUEUED_FORK_UNSUPPORTED"
