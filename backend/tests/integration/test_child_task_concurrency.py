"""Parent-bound child instructions serialize with concurrent chat actions in PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TypedDict
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.authz.org_context import OrgContext
from app.config import settings
from app.exceptions import ConflictError
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_child_task import ConversationChildInstruction
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_child_task import ChildInstructionCreate
from app.services import child_tasks_api as api
from app.services import child_tasks_runtime as runtime
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.child_task_errors import ChildTaskStopped
from app.services.conversation_run_state import transition_run
from tests.authz.execution_gateway_testkit import seed_graph
from tests.integration.test_conversation_queue_concurrency import _postgres_session_factory


class _RunScope(TypedDict):
    context: OrgContext
    run_id: UUID


class _ChildScope(_RunScope):
    child_id: UUID


@dataclass(frozen=True)
class Children:
    sessions: async_sessionmaker[AsyncSession]
    context: OrgContext
    conversation_id: UUID
    run_id: UUID
    agent_id: UUID
    child_agent_id: UUID
    first: UUID
    second: UUID


async def _accept(
    db: AsyncSession,
    context: OrgContext,
    agent_id: UUID,
    conversation_id: UUID,
    parent_run_id: UUID | None = None,
) -> UUID:
    request = OwnedExecutionRequest(
        root_agent_id=agent_id,
        conversation_id=conversation_id,
        context=context,
        source="chat",
        input_preview="resume approved" if parent_run_id else "research",
        run_source="resume" if parent_run_id else "chat",
        parent_run_id=parent_run_id,
        interrupt_id="approval" if parent_run_id else None,
    )
    accepted = await accept_owned_execution(db, request)
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None
    await transition_run(db, run, "running")
    await db.commit()
    return run.id


@pytest.fixture
async def children(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Children]:
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
    sessions, engine = _postgres_session_factory()
    try:
        async with sessions() as db:
            graph = await seed_graph(db, conversations=1)
            context, conversation_id = graph.context, graph.conversation_ids[0]
            agent = await db.get(Agent, graph.agent_id)
            assert agent is not None
            target = Agent(
                name="Research",
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                model_id=agent.model_id,
                system_prompt="Research.",
            )
            db.add(target)
            await db.flush()
            db.add(
                AgentSubAgentLink(
                    parent_agent_id=agent.id, sub_agent_id=target.id, delegated_by=context.user_id
                )
            )
            await db.commit()
            run_id = await _accept(db, context, agent.id, conversation_id)
            ids = []
            for call in ("first", "second"):
                ids.append(
                    await runtime.begin_child_task(
                        db,
                        context=context,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        tool_call_id=call,
                        child_agent_id=target.id,
                        role="research",
                        checkpoint_ns=f"tools:{call}",
                    )
                )
                await db.commit()
            yield Children(sessions, context, conversation_id, run_id, agent.id, target.id, *ids)
    finally:
        await engine.dispose()


async def _send(children: Children, child_id: UUID, key: str = "one-request") -> UUID:
    async with children.sessions() as db:
        view = await api.instruct_child(
            db,
            children.conversation_id,
            child_id,
            children.context,
            ChildInstructionCreate(client_request_id=key, text="국내 기업으로 좁혀줘"),
        )
    return next(item.id for item in view.instructions if item.client_request_id == key)


async def test_concurrent_duplicates_deliver_once_to_the_target(children: Children) -> None:
    results = await asyncio.gather(*(_send(children, children.first) for _ in range(4)))
    assert len(set(results)) == 1
    receipt_id = results[0]
    async with children.sessions() as db:
        args: _RunScope = {"context": children.context, "run_id": children.run_id}
        assert not await runtime.next_child_instructions(db, child_id=children.second, **args)
        await db.commit()
        assert await runtime.next_child_instructions(db, child_id=children.first, **args) == [
            (receipt_id, "국내 기업으로 좁혀줘", True)
        ]
        await runtime.confirm_child_instructions(
            db, child_id=children.first, ids={receipt_id}, checkpoint_id="verified-first", **args
        )
        await db.commit()
        assert not await runtime.next_child_instructions(db, child_id=children.first, **args)


async def _wait_for_database_lock(holder: AsyncSession, contender_pid: int) -> None:
    # Only PostgreSQL can tell us when the authenticated contender actually holds a wait slot.
    async with asyncio.timeout(15):
        while True:
            if await holder.scalar(
                text("SELECT pg_backend_pid() = ANY(pg_blocking_pids(:pid))"),
                {"pid": contender_pid},
            ):
                return
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("cancel_first", [True, False])
async def test_cancel_and_checkpoint_serialize_without_changing_sibling(
    children: Children,
    cancel_first: bool,
) -> None:
    receipt_id = await _send(children, children.first)
    sibling_receipt_id = await _send(children, children.second)
    args: _ChildScope = {
        "context": children.context,
        "run_id": children.run_id,
        "child_id": children.first,
    }
    async with children.sessions() as db:
        assert len(await runtime.next_child_instructions(db, **args)) == 1
        await db.commit()

    async def confirm(db: AsyncSession) -> None:
        await runtime.confirm_child_instructions(
            db, ids={receipt_id}, checkpoint_id="verified", **args
        )
        await db.commit()

    async def cancel(db: AsyncSession) -> None:
        await api.cancel_child(db, children.conversation_id, children.first, children.context)

    # Hold one side of the race open until PostgreSQL reports the other session is waiting.
    async with children.sessions() as holder, children.sessions() as contender:
        contender_pid = int((await contender.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        if cancel_first:
            await runtime.set_child_task_status(holder, status="cancelled", **args)
            pending = asyncio.create_task(confirm(contender))
            expected = "review_required"
        else:
            await runtime.confirm_child_instructions(
                holder, ids={receipt_id}, checkpoint_id="first", **args
            )
            pending = asyncio.create_task(cancel(contender))
            expected = "applied"
        try:
            await _wait_for_database_lock(holder, contender_pid)
            await holder.commit()
            if cancel_first:
                with pytest.raises(ChildTaskStopped) as stopped:
                    await pending
                assert stopped.value.reason == "cancelled"
            else:
                await pending
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    async with children.sessions() as db:
        statuses = dict(
            (
                await db.execute(
                    select(
                        ConversationChildInstruction.id, ConversationChildInstruction.status
                    ).where(ConversationChildInstruction.id.in_([receipt_id, sibling_receipt_id]))
                )
            )
            .tuples()
            .all()
        )
    assert statuses == {receipt_id: expected, sibling_receipt_id: "pending"}


async def test_resumed_child_rejects_worker_from_interrupted_parent(children: Children) -> None:
    async with children.sessions() as db:
        await runtime.set_child_task_status(
            db,
            context=children.context,
            child_id=children.first,
            run_id=children.run_id,
            status="awaiting_approval",
        )
        await db.commit()
        run = await db.get(ConversationRun, children.run_id, with_for_update=True)
        assert run is not None
        await transition_run(db, run, "interrupted", interrupt_id="approval")
        await db.commit()
    receipt = await _send(children, children.first)
    async with children.sessions() as db:
        resumed = await _accept(
            db, children.context, children.agent_id, children.conversation_id, children.run_id
        )
        assert (
            await runtime.begin_child_task(
                db,
                context=children.context,
                conversation_id=children.conversation_id,
                run_id=resumed,
                tool_call_id="first",
                child_agent_id=children.child_agent_id,
                role="research",
                checkpoint_ns="tools:first",
            )
            == children.first
        )
        await db.commit()
        with pytest.raises(ChildTaskStopped):
            await runtime.confirm_child_instructions(
                db,
                context=children.context,
                run_id=children.run_id,
                child_id=children.first,
                ids={receipt},
                checkpoint_id="stale",
            )
        await db.rollback()
        assert await runtime.next_child_instructions(
            db, context=children.context, run_id=resumed, child_id=children.first
        ) == [(receipt, "국내 기업으로 좁혀줘", True)]


async def test_parent_close_fences_late_instructions_and_settles_both_children(
    children: Children,
) -> None:
    pending_id = await _send(children, children.first)
    attempted_id = await _send(children, children.second)
    async with children.sessions() as holder, children.sessions() as contender:
        await runtime.next_child_instructions(
            holder, context=children.context, child_id=children.second, run_id=children.run_id
        )
        await holder.commit()
        contender_pid = int((await contender.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        run = await holder.get(ConversationRun, children.run_id, with_for_update=True)
        assert run is not None
        await transition_run(holder, run, "completed")
        late = asyncio.create_task(
            api.instruct_child(
                contender,
                children.conversation_id,
                children.first,
                children.context,
                ChildInstructionCreate(client_request_id="late", text="추가 지시"),
            )
        )
        try:
            await _wait_for_database_lock(holder, contender_pid)
            await holder.commit()
            with pytest.raises(ConflictError) as rejected:
                await late
            assert rejected.value.code == "CHILD_TASK_CLOSED"
        finally:
            if not late.done():
                late.cancel()
                await asyncio.gather(late, return_exceptions=True)
    async with children.sessions() as db:
        views = await api.list_child_tasks(db, children.conversation_id, children.context)
        assert len(views) == 2 and all(row.status == "cancelled" for row in views)
        statuses = {receipt.id: receipt.status for row in views for receipt in row.instructions}
        assert statuses == {pending_id: "cancelled", attempted_id: "review_required"}
