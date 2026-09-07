from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.services import conversation_run_queue_service, conversation_run_service
from app.services.conversation_run_queue_promotion import promote_pending_input
from tests.conftest import TEST_USER_ID, register_session
from tests.integration._seed import seed_conversation_with_agent


async def _pending(db: AsyncSession, conversation_id: uuid.UUID, *, request_id: str = "queue"):
    return await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id=request_id,
        source="chat",
        input_payload={"messages": [{"role": "user", "content": request_id}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )


@pytest.mark.asyncio
async def test_promote_pending_input_requests_predecessor_cancel_atomically(
    db: AsyncSession,
) -> None:
    # Given an active predecessor and an ordinary pending input.
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
    await conversation_run_service.transition_run(db, active, "running", worker_instance_id="owner")
    queued = await _pending(db, conversation_id)

    # When the pending input is promoted with its current revision.
    result = await promote_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=queued.id,
        user_id=TEST_USER_ID,
        expected_revision=1,
    )

    # Then priority/revision and predecessor cancellation intent coexist before commit.
    assert result.input.priority == 100
    assert result.input.revision == 2
    assert result.predecessor_run_id == active.id
    assert active.cancel_requested_at is not None
    assert active.cancel_reason == "steer"


@pytest.mark.asyncio
async def test_promote_pending_input_without_active_run_is_dispatchable(
    db: AsyncSession,
) -> None:
    # Given an idle conversation with one pending input.
    conversation_id = await seed_conversation_with_agent()
    queued = await _pending(db, conversation_id)

    # When the input is promoted.
    result = await promote_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=queued.id,
        user_id=TEST_USER_ID,
        expected_revision=1,
    )

    # Then the router can dispatch after commit without a cancellation side effect.
    assert result.input.priority == 100
    assert result.predecessor_run_id is None


@pytest.mark.asyncio
async def test_promote_pending_input_rejects_replay_before_canceling_again(
    db: AsyncSession,
) -> None:
    # Given an active predecessor and an input already promoted once.
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
    await conversation_run_service.transition_run(db, active, "running", worker_instance_id="owner")
    queued = await _pending(db, conversation_id)
    await promote_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=queued.id,
        user_id=TEST_USER_ID,
        expected_revision=1,
    )
    first_cancel_requested_at = active.cancel_requested_at

    # When the client repeats promotion with the newly returned revision.
    with pytest.raises(HTTPException) as exc:
        await promote_pending_input(
            db,
            conversation_id=conversation_id,
            input_id=queued.id,
            user_id=TEST_USER_ID,
            expected_revision=2,
        )

    # Then it fails before changing revision or cancellation intent.
    assert exc.value.status_code == 409
    assert queued.revision == 2
    assert active.cancel_requested_at == first_cancel_requested_at


@pytest.mark.asyncio
async def test_promote_pending_input_rejects_paused_queue(
    db: AsyncSession,
) -> None:
    # Given a stopped, paused queue with one pending input.
    conversation_id = await seed_conversation_with_agent()
    queued = await _pending(db, conversation_id)
    await conversation_run_queue_service.pause_queue(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
    )

    # When promotion is requested before explicit resume.
    with pytest.raises(HTTPException) as exc:
        await promote_pending_input(
            db,
            conversation_id=conversation_id,
            input_id=queued.id,
            user_id=TEST_USER_ID,
            expected_revision=1,
        )

    # Then stop semantics remain authoritative and the input is unchanged.
    assert exc.value.status_code == 409
    assert queued.priority == 0
    assert queued.revision == 1


@pytest.mark.asyncio
async def test_promote_pending_input_hides_foreign_conversation(
    db: AsyncSession,
) -> None:
    # Given a conversation and pending input owned by another account.
    conversation_id = await seed_conversation_with_agent()
    queued = await _pending(db, conversation_id)

    # When a different user attempts promotion through the service boundary.
    with pytest.raises(HTTPException) as exc:
        await promote_pending_input(
            db,
            conversation_id=conversation_id,
            input_id=queued.id,
            user_id=uuid.uuid4(),
            expected_revision=1,
        )

    # Then ownership is enumeration-safe and the durable input is untouched.
    assert exc.value.status_code == 404
    assert queued.priority == 0
    assert queued.revision == 1


@pytest.mark.asyncio
async def test_promote_route_requires_csrf(raw_client: AsyncClient) -> None:
    # Given an authenticated cookie session without a matching CSRF header.
    await register_session(raw_client, email="queue-promote-csrf@test.com", name="Queue CSRF")

    # When promotion is attempted against any identifier.
    response = await raw_client.post(
        f"/api/conversations/{uuid.uuid4()}/run-inputs/{uuid.uuid4()}/promote",
        json={"expected_revision": 1},
    )

    # Then the mutation is rejected at the CSRF boundary before resource lookup.
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_mismatch"


@pytest.mark.asyncio
async def test_promote_route_dispatches_idle_input_after_commit(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an idle conversation with one ordinary pending input.
    conversation_id = await seed_conversation_with_agent()
    queued = await _pending(db, conversation_id)
    await db.commit()
    dispatched: list[uuid.UUID] = []

    async def record_dispatch(value: uuid.UUID) -> None:
        dispatched.append(value)

    monkeypatch.setattr(
        "app.routers.conversation_run_inputs.dispatch_next_for_conversation",
        record_dispatch,
    )

    # When the owner promotes it through the HTTP contract.
    response = await client.post(
        f"/api/conversations/{conversation_id}/run-inputs/{queued.id}/promote",
        json={"expected_revision": 1},
    )

    # Then the committed promotion is returned and idle dispatch is triggered exactly once.
    assert response.status_code == 200
    assert response.json()["priority"] == 100
    assert response.json()["revision"] == 2
    assert dispatched == [conversation_id]


@pytest.mark.asyncio
async def test_promote_route_rejects_repeat_without_dispatch_or_second_cancel(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an idle input already promoted through the service.
    conversation_id = await seed_conversation_with_agent()
    queued = await _pending(db, conversation_id)
    await promote_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=queued.id,
        user_id=TEST_USER_ID,
        expected_revision=1,
    )
    await db.commit()
    dispatched: list[uuid.UUID] = []

    async def record_dispatch(value: uuid.UUID) -> None:
        dispatched.append(value)

    monkeypatch.setattr(
        "app.routers.conversation_run_inputs.dispatch_next_for_conversation",
        record_dispatch,
    )

    # When the newly returned revision is replayed as another promotion.
    response = await client.post(
        f"/api/conversations/{conversation_id}/run-inputs/{queued.id}/promote",
        json={"expected_revision": 2},
    )

    # Then it fails honestly without dispatching or mutating the item again.
    assert response.status_code == 409
    assert dispatched == []
    await db.refresh(queued)
    assert queued.revision == 2
