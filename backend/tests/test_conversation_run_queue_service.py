from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_queue_service
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent


async def _enqueue(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    client_request_id: str,
    content: str,
) -> ConversationRunInput:
    return await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        client_request_id=client_request_id,
        source="chat",
        input_payload={"messages": [{"role": "user", "content": content}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_by_conversation_request_id(db: AsyncSession) -> None:
    # Given a conversation and one accepted pending input.
    conversation_id = await seed_conversation_with_agent()
    first = await _enqueue(db, conversation_id, "request-1", "first")

    # When the client retries the same request.
    retried = await _enqueue(db, conversation_id, "request-1", "first")

    # Then the original pending row is returned instead of duplicating work.
    assert retried.id == first.id
    assert retried.run_id is None
    assert retried.status == "pending"


@pytest.mark.asyncio
async def test_pending_inputs_support_revisioned_edit_delete_and_reorder(
    db: AsyncSession,
) -> None:
    # Given three FIFO pending inputs.
    conversation_id = await seed_conversation_with_agent()
    first = await _enqueue(db, conversation_id, "request-1", "first")
    second = await _enqueue(db, conversation_id, "request-2", "second")
    third = await _enqueue(db, conversation_id, "request-3", "third")

    # When the second input is edited, the queue reordered, and the third removed.
    edited = await conversation_run_queue_service.edit_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=second.id,
        user_id=TEST_USER_ID,
        expected_revision=1,
        input_payload={"messages": [{"role": "user", "content": "second edited"}]},
    )
    await conversation_run_queue_service.reorder_pending_inputs(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        ordered_input_ids=[second.id, first.id, third.id],
        expected_revisions={second.id: 2, first.id: 1, third.id: 1},
    )
    removed = await conversation_run_queue_service.delete_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=third.id,
        user_id=TEST_USER_ID,
        expected_revision=2,
    )

    # Then revisions advance and only the requested FIFO order remains.
    pending = await conversation_run_queue_service.list_inputs(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
    )
    assert edited.revision == 3
    assert removed.status == "canceled"
    assert [item.id for item in pending if item.status == "pending"] == [second.id, first.id]


@pytest.mark.asyncio
async def test_claim_allocates_run_only_after_conversation_locked(db: AsyncSession) -> None:
    # Given a persisted input with no lifecycle run.
    conversation_id = await seed_conversation_with_agent()
    queued = await _enqueue(db, conversation_id, "request-claim", "claim me")
    await db.commit()

    # When a worker claims the next pending input.
    claimed = await conversation_run_queue_service.claim_next_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
    )

    # Then claim atomically binds exactly one active run.
    assert claimed is not None
    assert claimed.input.id == queued.id
    assert claimed.input.status == "claimed"
    assert claimed.input.run_id == claimed.run.id
    assert claimed.run.is_active is True


@pytest.mark.asyncio
async def test_claimed_input_is_immutable(db: AsyncSession) -> None:
    # Given an input already bound to a run.
    conversation_id = await seed_conversation_with_agent()
    queued = await _enqueue(db, conversation_id, "request-fixed", "fixed")
    await db.commit()
    claimed = await conversation_run_queue_service.claim_next_input(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
    )
    assert claimed is not None

    # When a client tries to edit the claimed input.
    with pytest.raises(HTTPException) as exc:
        await conversation_run_queue_service.edit_pending_input(
            db,
            conversation_id=conversation_id,
            input_id=queued.id,
            user_id=TEST_USER_ID,
            expected_revision=2,
            input_payload={"messages": [{"role": "user", "content": "changed"}]},
        )

    # Then the server rejects mutation without changing the bound payload.
    assert exc.value.status_code == 409
