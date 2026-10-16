"""Run creation reuses one transaction proof; it never re-expands the prefix."""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.execution_lock_cursor import ExecutionProofError, LockClass
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.authz.organization_execution_proof import live_proof
from app.services import conversation_run_service
from tests.authz.execution_gateway_testkit import seed_graph


@pytest.mark.asyncio
async def test_compat_create_run_prelocks_batch_and_reuses_it_sql_free(db: AsyncSession) -> None:
    graph = await seed_graph(db, conversations=2)
    first_id, second_id = graph.conversation_ids
    await conversation_run_service.create_run(
        db,
        conversation_id=first_id,
        agent_id=graph.agent_id,
        context=graph.context,
        source="chat",
        input_preview="first",
    )
    proof = live_proof(db)
    assert proof is not None and proof.conversation_ids == graph.conversation_ids
    held_before = dict(proof.cursor._held)
    locks: list[str] = []
    sync = db.sync_session

    def spy(orm_execute_state) -> None:
        if getattr(orm_execute_state.statement, "_for_update_arg", None) is not None:
            locks.append(str(orm_execute_state.statement))

    event.listen(sync, "do_orm_execute", spy)
    try:
        await conversation_run_service.create_run(
            db,
            conversation_id=second_id,
            agent_id=graph.agent_id,
            context=graph.context,
            source="chat",
            input_preview="second",
        )
    finally:
        event.remove(sync, "do_orm_execute", spy)

    assert live_proof(db) is proof
    assert locks == []
    assert dict(proof.cursor._held) == held_before


@pytest.mark.asyncio
async def test_rejects_reverse_or_post_run_scope_expansion(db: AsyncSession) -> None:
    graph = await seed_graph(db, conversations=2)
    low, high = graph.conversation_ids
    await acquire_execution_proof(
        db, graph.context, root_agent_id=graph.agent_id, conversation_ids=(high,)
    )
    with pytest.raises(ExecutionProofError) as reverse:
        await acquire_execution_proof(db, graph.context, conversation_ids=(low,))
    assert reverse.value.code == "execution_scope_expansion_denied"
    await db.rollback()

    run = await conversation_run_service.create_run(
        db,
        conversation_id=low,
        agent_id=graph.agent_id,
        context=graph.context,
        source="chat",
        input_preview="low",
    )
    await db.commit()
    locked = await lock_executable_run(
        db,
        RunExecutionTarget(run.id, RunConversationTarget(low, graph.agent_id, graph.context)),
    )
    assert locked is not None
    proof = live_proof(db)
    assert proof is not None and proof.cursor.position is LockClass.RUN
    with pytest.raises(ExecutionProofError) as after_run:
        await acquire_execution_proof(db, graph.context, conversation_ids=(high,))
    assert after_run.value.code == "execution_scope_expansion_denied"


@pytest.mark.asyncio
async def test_missing_conversation_keeps_opaque_404(db: AsyncSession) -> None:
    import uuid

    from fastapi import HTTPException

    graph = await seed_graph(db, conversations=1)
    with pytest.raises(HTTPException) as missing:
        await conversation_run_service.create_run(
            db,
            conversation_id=uuid.uuid4(),
            agent_id=graph.agent_id,
            context=graph.context,
            source="chat",
            input_preview=None,
        )
    assert missing.value.status_code == 404
