"""Execution proofs are bound to one live root transaction and its lock discipline."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_lock_cursor import ExecutionLockCursor, ExecutionProofError, LockClass
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.authz.organization_execution_proof import live_proof
from app.models.conversation import Conversation
from tests.authz.execution_gateway_testkit import seed_graph
from tests.conftest import TestSession


async def _proof(db: AsyncSession):
    graph = await seed_graph(db)
    proof = await acquire_execution_proof(
        db, graph.context, root_agent_id=graph.agent_id, conversation_ids=graph.conversation_ids
    )
    return graph, proof


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["commit", "rollback"])
async def test_proof_dies_with_root_transaction(db: AsyncSession, ending: str) -> None:
    _graph, proof = await _proof(db)
    proof.require_live(db)

    await getattr(db, ending)()

    with pytest.raises(ExecutionProofError) as stale:
        proof.require_live(db)
    assert stale.value.code == "execution_proof_stale"
    assert live_proof(db) is None


@pytest.mark.asyncio
async def test_savepoint_creation_and_end_invalidate_proof(db: AsyncSession) -> None:
    _graph, proof = await _proof(db)

    nested = await db.begin_nested()
    with pytest.raises(ExecutionProofError):
        proof.require_live(db)
    await nested.commit()
    with pytest.raises(ExecutionProofError):
        proof.require_live(db)


@pytest.mark.asyncio
async def test_wrong_session_and_untracked_lower_lock_fail_closed(db: AsyncSession) -> None:
    graph, proof = await _proof(db)
    async with TestSession() as other:
        with pytest.raises(ExecutionProofError) as wrong:
            proof.require_live(other)
    assert wrong.value.code == "execution_proof_stale"

    await db.scalar(
        select(Conversation).where(Conversation.id == graph.conversation_ids[0]).with_for_update()
    )
    with pytest.raises(ExecutionProofError) as untracked:
        proof.require_live(db)
    assert untracked.value.code == "untracked_lower_lock"


@pytest.mark.asyncio
async def test_prior_lower_lock_requires_proof_first(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    await db.scalar(
        select(Conversation).where(Conversation.id == graph.conversation_ids[0]).with_for_update()
    )

    with pytest.raises(ExecutionProofError) as required:
        await acquire_execution_proof(db, graph.context, root_agent_id=graph.agent_id)
    assert required.value.code == "execution_proof_required"


def test_cursor_rejects_earlier_class_and_reverse_uuid_but_reuses_exact() -> None:
    import uuid

    low, high = uuid.UUID(int=1), uuid.UUID(int=2)
    cursor = ExecutionLockCursor()
    assert cursor.admit(LockClass.CONVERSATION, low)
    cursor.record(LockClass.CONVERSATION, low, "row")

    assert cursor.admit(LockClass.CONVERSATION, low) is False
    assert cursor.held_row(LockClass.CONVERSATION, low) == "row"
    assert cursor.admit(LockClass.CONVERSATION, high)
    cursor.record(LockClass.CONVERSATION, high)
    for lock_class, resource in ((LockClass.AGENT_LIFECYCLE, high), (LockClass.CONVERSATION, low)):
        if cursor.holds(lock_class, resource):
            continue
        with pytest.raises(ExecutionProofError) as denied:
            cursor.admit(lock_class, resource)
        assert denied.value.code == "execution_scope_expansion_denied"
    with pytest.raises(ExecutionProofError):
        cursor.admit(LockClass.CONVERSATION, uuid.UUID(int=0))
    cursor.record(LockClass.INPUT, high)
    assert cursor.admit(LockClass.INPUT, low)  # serialized by the held Conversation lock
