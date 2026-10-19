"""Runtime policy never locks Agent after Conversation; it consumes the proof's locks."""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_lock_cursor import LockClass
from app.authz.organization_execution_proof import live_proof
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy
from tests.authz.execution_gateway_testkit import seed_graph


@pytest.mark.asyncio
async def test_policy_locks_lifecycle_before_conversation_and_never_agent_rows(
    db: AsyncSession,
) -> None:
    graph = await seed_graph(db, conversations=1)
    tables: list[set[str]] = []
    sync = db.sync_session

    def spy(orm_execute_state) -> None:
        statement = orm_execute_state.statement
        if getattr(statement, "_for_update_arg", None) is not None:
            tables.append({getattr(item, "name", "") for item in statement.get_final_froms()})

    event.listen(sync, "do_orm_execute", spy)
    try:
        _conversation, agent, resolved = await ensure_conversation_runtime_policy(
            db, ConversationPolicyTarget(graph.conversation_ids[0], graph.context)
        )
    finally:
        event.remove(sync, "do_orm_execute", spy)

    assert agent.id == graph.agent_id and resolved is not None
    assert all("agents" not in names for names in tables)
    assert tables[-1] == {"conversations"}
    proof = live_proof(db)
    assert proof is not None
    classes = [lock_class for lock_class, _ in proof.cursor._held]
    assert classes.index(LockClass.AGENT_LIFECYCLE) < classes.index(LockClass.CONVERSATION)
    assert classes.index(LockClass.USER) < classes.index(LockClass.AGENT_LIFECYCLE)
