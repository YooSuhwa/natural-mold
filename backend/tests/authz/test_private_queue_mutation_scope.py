"""Queue changes require the exact conversation scope, even for the same user."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import conversation_run_queue_service as queue

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_enqueue_rejects_same_owner_foreign_company(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    with pytest.raises(HTTPException) as captured:
        await queue.enqueue_input(
            db,
            conversation_id=collector_case.conversation.id,
            context=private_scopes.second,
            client_request_id="foreign-queue",
            source="chat",
            input_payload={"messages": [{"role": "user", "content": "Fixture"}]},
            attachment_ids=[],
            checkpoint_id=None,
            priority=0,
        )
    assert captured.value.status_code == 404


async def test_pause_rejects_foreign_scope_before_mutation(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    with pytest.raises(HTTPException) as captured:
        await queue.pause_queue(
            db, conversation_id=collector_case.conversation.id, context=private_scopes.second
        )
    assert captured.value.status_code == 404
    await db.refresh(collector_case.conversation)
    assert collector_case.conversation.queue_paused is False


async def test_queue_writer_and_list_use_one_exact_parent_scope(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    queued = await queue.enqueue_input(
        db,
        conversation_id=collector_case.conversation.id,
        context=private_scopes.first,
        client_request_id="own-queue",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "Fixture"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    assert queued.user_id == private_scopes.first.user_id
    assert await queue.list_inputs(
        db, conversation_id=collector_case.conversation.id, context=private_scopes.first
    ) == [queued]
    with pytest.raises(HTTPException):
        await queue.claim_next_input(
            db, conversation_id=collector_case.conversation.id, context=private_scopes.second
        )
    assert queued.run_id is None and queued.status == "pending"
