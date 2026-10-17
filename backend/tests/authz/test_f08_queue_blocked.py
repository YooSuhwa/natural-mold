"""F-08 (plan 11.6): the queue worker re-decides ``agent#can_run`` before every claim.

A definite denial pauses the queue (input stays pending, ``run.blocked`` audited once,
committed so the recovery tick does not repeat it); OpenFGA down writes nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.services import conversation_run_queue_service
from tests.authz.test_conversation_route_authz import _service, install
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent
from tests.test_conversation_run_queue_service import _enqueue

__all__ = ["install"]


async def _blocked_audits() -> list[AuditEvent]:
    async with TestSession() as check:
        return list(
            await check.scalars(select(AuditEvent).where(AuditEvent.action == "run.blocked"))
        )


async def _paused(conversation_id) -> bool:
    async with TestSession() as check:
        conversation = await check.get(Conversation, conversation_id)
        assert conversation is not None
        return conversation.queue_paused


async def _claim(db, conversation_id):
    return await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, context=unit_context(TEST_USER_ID)
    )


@pytest.mark.asyncio
async def test_denied_queue_dispatch_pauses_and_audits_once(db, install) -> None:
    conversation_id = await seed_conversation_with_agent()
    queued = await _enqueue(db, conversation_id, "f08-q", "claim me")
    await db.commit()
    install(_service("enforce"))
    assert await _claim(db, conversation_id) is None
    assert await _claim(db, conversation_id) is None  # paused: no second decision/audit
    await db.refresh(queued)
    assert queued.status == "pending" and queued.run_id is None
    assert await _paused(conversation_id)
    [audit] = await _blocked_audits()
    assert (audit.reason_code, audit.outcome) == ("permission_revoked", "denied")
    assert audit.target_owner_user_id == TEST_USER_ID  # the agent's owner
    assert audit.event_metadata is not None
    assert audit.event_metadata["invoker_type"] == "queue"
    assert audit.event_metadata["source_id"] == str(conversation_id)


@pytest.mark.asyncio
async def test_unavailable_authz_leaves_the_queue_untouched(db, install) -> None:
    conversation_id = await seed_conversation_with_agent()
    queued = await _enqueue(db, conversation_id, "f08-q2", "later")
    await db.commit()
    install(_service("enforce", unavailable=True))
    assert await _claim(db, conversation_id) is None
    await db.refresh(queued)
    assert queued.status == "pending"
    assert not await _paused(conversation_id)
    assert await _blocked_audits() == []
