"""Prove live edit/retry branch changes against actual root checkpoints and private rows."""

from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.authz.org_context import OrgContext
from app.services.ag_ui_read_branch import accepts_fork_handoff
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run
from tests.authz.unit_scope_fixture import unit_context


@pytest.mark.parametrize("operation", ["edit", "regenerate"])
async def test_fork_accepts_only_its_last_stamped_root_checkpoint(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    run, _, room = await seed_runtime_run()
    run = await db.merge(run)
    room = await db.merge(room)
    actor = unit_context(run.user_id)
    saver = InMemorySaver()
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)

    def reply(state: MessagesState) -> MessagesState:
        return {"messages": [AIMessage(content=f"reply {len(state['messages'])}")]}

    graph = (
        StateGraph(MessagesState)
        .add_node("reply", reply)
        .add_edge(START, "reply")
        .add_edge("reply", END)
        .compile(checkpointer=saver)
    )
    config: RunnableConfig = {
        "configurable": run_configurable(str(room.id), run.id, actor.tenant_id)
    }
    await graph.ainvoke({"messages": [HumanMessage(content="edit")]}, config)
    own_heads = [
        s.config.get("configurable", {})["checkpoint_id"]
        async for s in graph.aget_state_history(config)
    ]
    assert len(own_heads) > 1
    run.source = operation
    run.status = "completed"
    run.is_active = False
    room.active_branch_checkpoint_id = own_heads[0]
    await db.commit()
    assert await accepts_fork_handoff(db, room, actor, run)

    # Selecting an older checkpoint from the same fork changes the user-visible branch.
    room.active_branch_checkpoint_id = own_heads[1]
    await db.commit()
    assert not await accepts_fork_handoff(db, room, actor, run)

    for foreign_run, foreign_tenant in ((uuid4(), actor.tenant_id), (run.id, uuid4())):
        other: RunnableConfig = {
            "configurable": run_configurable(str(room.id), foreign_run, foreign_tenant)
        }
        await graph.ainvoke({"messages": [HumanMessage(content="other")]}, other)
        room.active_branch_checkpoint_id = (await graph.aget_state(other)).config.get(
            "configurable", {}
        )["checkpoint_id"]
        await db.commit()
        assert not await accepts_fork_handoff(db, room, actor, run)

    room.active_branch_checkpoint_id = own_heads[0]
    await db.commit()
    foreign = OrgContext(org_id=uuid4(), tenant_id=actor.tenant_id, user_id=actor.user_id)
    assert not await accepts_fork_handoff(db, room, foreign, run)
    for source, status in ((operation, "running"), ("chat", "completed"), (operation, "failed")):
        run.source, run.status = source, status
        await db.commit()
        assert not await accepts_fork_handoff(db, room, actor, run)


async def test_missing_saver_cannot_authorize_branch_handoff(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _, room = await seed_runtime_run()
    run = await db.merge(run)
    room = await db.merge(room)
    run.source, run.status, run.is_active = "edit", "completed", False
    room.active_branch_checkpoint_id = "missing-checkpoint"
    await db.commit()

    def unavailable() -> Any:
        raise RuntimeError("offline")

    monkeypatch.setattr(checkpointer, "get_checkpointer", unavailable)
    assert not await accepts_fork_handoff(db, room, unit_context(run.user_id), run)
