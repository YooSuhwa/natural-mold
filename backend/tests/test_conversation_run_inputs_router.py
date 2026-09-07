from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.services import conversation_run_queue_service, conversation_run_service
from app.services.conversation_run_queue_payload import MAX_QUEUE_INPUT_BYTES
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent


@pytest.mark.asyncio
async def test_queue_routes_edit_reorder_delete_pending_inputs(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    # Given two durable pending inputs owned by the current user.
    conversation_id = await seed_conversation_with_agent()
    first = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="route-1",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "one"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    second = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="route-2",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "two"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()

    # When the owner edits, reorders, then removes through the HTTP contract.
    edited = await client.patch(
        f"/api/conversations/{conversation_id}/run-inputs/{second.id}",
        json={
            "expected_revision": 1,
            "input": {"messages": [{"role": "user", "content": "two edited"}]},
        },
    )
    reordered = await client.post(
        f"/api/conversations/{conversation_id}/run-inputs/reorder",
        json={
            "ordered_input_ids": [str(second.id), str(first.id)],
            "expected_revisions": {str(second.id): 2, str(first.id): 1},
        },
    )
    removed = await client.delete(
        f"/api/conversations/{conversation_id}/run-inputs/{first.id}",
        params={"expected_revision": 2},
    )

    # Then each revisioned mutation is durable and list order is authoritative.
    assert edited.status_code == 200
    assert reordered.status_code == 200
    assert removed.status_code == 200
    response = await client.get(f"/api/conversations/{conversation_id}/run-inputs")
    assert response.status_code == 200
    assert response.json()["queue_paused"] is False
    assert [item["id"] for item in response.json()["items"]] == [str(second.id), str(first.id)]
    assert response.json()["items"][1]["status"] == "canceled"


@pytest.mark.asyncio
async def test_queue_routes_hide_foreign_or_missing_conversation(client: AsyncClient) -> None:
    # Given an unowned conversation identifier.
    missing_id = uuid.uuid4()

    # When queue state is requested.
    response = await client.get(f"/api/conversations/{missing_id}/run-inputs")

    # Then the response is enumeration-safe.
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_queue_edit_rejects_attachment_replacement(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    attachment_id = uuid.uuid4()
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="attached",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "attached"}]},
        attachment_ids=[attachment_id],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()

    response = await client.patch(
        f"/api/conversations/{conversation_id}/run-inputs/{queued.id}",
        json={
            "expected_revision": 1,
            "input": {
                "messages": [{"role": "user", "content": "changed"}],
                "attachments": [{"id": str(uuid.uuid4())}],
            },
        },
    )

    assert response.status_code == 409
    await db.refresh(queued)
    assert queued.revision == 1
    assert queued.attachment_ids == [str(attachment_id)]


@pytest.mark.asyncio
async def test_queue_edit_rejects_malformed_and_oversized_json_input(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="bounded-edit",
        source="chat",
        input_payload={"messages": []},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    endpoint = f"/api/conversations/{conversation_id}/run-inputs/{queued.id}"

    malformed = await client.patch(
        endpoint,
        json={"expected_revision": 1, "input": ["not", "an", "object"]},
    )
    oversized = await client.patch(
        endpoint,
        json={
            "expected_revision": 1,
            "input": {"messages": [{"content": "x" * MAX_QUEUE_INPUT_BYTES}]},
        },
    )

    assert malformed.status_code == 422
    assert oversized.status_code == 422
    await db.refresh(queued)
    assert queued.revision == 1
    assert queued.input_payload == {"messages": []}


@pytest.mark.asyncio
async def test_stop_pauses_dispatch_until_owner_resumes(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an active run and a normal pending follow-up.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        input_preview="active",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="remote")
    await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id="after-stop",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "later"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()

    # When stop is requested and the queue is later resumed.
    stopped = await client.post(f"/api/conversations/{conversation_id}/runs/{run.id}/cancel")
    dispatched: list[uuid.UUID] = []

    async def record_dispatch(value: uuid.UUID):
        dispatched.append(value)

    monkeypatch.setattr(
        "app.routers.conversation_run_inputs.dispatch_next_for_conversation",
        record_dispatch,
    )
    resumed = await client.post(f"/api/conversations/{conversation_id}/run-inputs/resume")

    # Then stop is durable pause and resume explicitly re-enables dispatch.
    assert stopped.json()["status"] == "canceling"
    assert stopped.json()["cancel_reason"] == "stop"
    assert resumed.status_code == 200
    assert resumed.json()["queue_paused"] is False
    assert dispatched == [conversation_id]
