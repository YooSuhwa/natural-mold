"""A replaced chat worker cannot claim or acknowledge an instruction on its old turn."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat_recovery.fence import RunOwnershipLost, claim_run_owner, run_fence_scope
from app.models.conversation_run import ConversationRun
from app.services.conversation_run_steers import claim_steers_for_model, confirm_applied_steers
from tests.sqlite_test_engine import TestSession
from tests.test_conversation_run_steers_service import _admit, _running


async def test_replaced_worker_cannot_claim_or_confirm_a_steer(db: AsyncSession) -> None:
    run = await _running(db)
    original = claim_run_owner(run, "worker")
    steer = await _admit(db, run, "Use domestic suppliers")
    await db.commit()
    conversation_id, run_id = run.conversation_id, run.id

    # Keep the original session's stale ORM row. A new process may reuse the worker name.
    async with TestSession() as other:
        locked = await other.get(ConversationRun, run_id, with_for_update=True)
        assert locked is not None
        successor = claim_run_owner(locked, original.worker_instance_id)
        await other.commit()
    assert successor.generation == original.generation + 1
    assert successor.owner_token != original.owner_token
    with run_fence_scope(original), pytest.raises(RunOwnershipLost):
        await claim_steers_for_model(db, conversation_id=conversation_id, run_id=run_id)
    await db.refresh(steer)
    assert steer.delivery_started_at is None
    with run_fence_scope(successor):
        claims = await claim_steers_for_model(db, conversation_id=conversation_id, run_id=run_id)
    assert [row.id for row in claims] == [steer.id]
    await db.commit()
    for invalid_fence in (
        original,
        replace(successor, owner_token=uuid4()),
        replace(successor, generation=original.generation),
        replace(successor, tenant=replace(successor.tenant, org_id=uuid4())),
    ):
        with run_fence_scope(invalid_fence), pytest.raises(RunOwnershipLost):
            await confirm_applied_steers(
                db, conversation_id=conversation_id, run_id=run_id, steer_ids={steer.id}
            )
        await db.refresh(steer)
        assert steer.status == "pending" and steer.applied_at is None
    with run_fence_scope(successor):
        await confirm_applied_steers(
            db, conversation_id=conversation_id, run_id=run_id, steer_ids={steer.id}
        )
    await db.refresh(steer)
    assert steer.status == "applied" and steer.applied_at is not None


async def test_chat_outside_worker_recovery_can_claim_and_confirm(db: AsyncSession) -> None:
    run = await _running(db)
    steer = await _admit(db, run, "Use domestic suppliers")
    await db.commit()
    with run_fence_scope(None):
        claims = await claim_steers_for_model(
            db, conversation_id=run.conversation_id, run_id=run.id
        )
        assert [row.id for row in claims] == [steer.id]
        await confirm_applied_steers(
            db, conversation_id=run.conversation_id, run_id=run.id, steer_ids={steer.id}
        )
    await db.refresh(steer)
    assert steer.status == "applied"
