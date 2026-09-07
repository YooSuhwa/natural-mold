from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_service
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent


async def _seed_active_run(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    conversation_id = await seed_conversation_with_agent()
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
    await conversation_run_service.transition_run(
        db,
        active,
        "running",
        worker_instance_id="remote-worker",
    )
    await db.commit()
    return conversation_id, active.id


@pytest.mark.asyncio
async def test_run_start_rejects_null_resource_context_before_enqueue(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation_id, active_run_id = await _seed_active_run(db)
    endpoint = f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands"

    response = await client.post(
        endpoint,
        json={
            "id": "null-context-start",
            "method": "run.start",
            "params": {
                "client_request_id": "null-context-start",
                "multitask_strategy": "enqueue",
                "input": {
                    "messages": [{"role": "user", "content": "hello"}],
                    "resource_context": None,
                },
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RESOURCE_CONTEXT_INVALID"
    queued = await db.scalar(
        select(ConversationRunInput).where(
            ConversationRunInput.client_request_id == "null-context-start"
        )
    )
    assert queued is None
    runs = (
        await db.scalars(
            select(ConversationRun).where(ConversationRun.conversation_id == conversation_id)
        )
    ).all()
    assert [run.id for run in runs] == [active_run_id]


@pytest.mark.asyncio
async def test_queue_patch_rejects_null_context_without_mutating_input(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation_id, _active_run_id = await _seed_active_run(db)
    command_endpoint = (
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands"
    )
    accepted = await client.post(
        command_endpoint,
        json={
            "id": "null-context-edit",
            "method": "run.start",
            "params": {
                "client_request_id": "null-context-edit",
                "multitask_strategy": "enqueue",
                "input": {"messages": [{"role": "user", "content": "original"}]},
            },
        },
    )
    assert accepted.status_code == 200
    input_id = uuid.UUID(accepted.json()["result"]["input_id"])
    queued = await db.get(ConversationRunInput, input_id)
    assert queued is not None
    original_payload = queued.input_payload

    response = await client.patch(
        f"/api/conversations/{conversation_id}/run-inputs/{input_id}",
        json={
            "expected_revision": 1,
            "input": {
                "messages": [{"role": "user", "content": "mutated"}],
                "resource_context": None,
            },
        },
    )

    assert response.status_code == 422
    await db.refresh(queued)
    assert queued.revision == 1
    assert queued.input_payload == original_payload
