"""A duplicate handoff racing on two PostgreSQL connections owns a single execution."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.authz.org_context import OrgContext
from app.config import settings
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.independent_chat_job import IndependentChatJob
from app.models.memory import AgentMemorySettings
from app.schemas.independent_chat_job import IndependentJobCreate
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_run_service import transition_run
from app.services.independent_jobs import accept_independent_job
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.test_conversation_queue_concurrency import (
    _postgres_session_factory,
    _seed_conversation,
)


async def _accepted_parent(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[OrgContext, UUID, UUID, UUID]:
    owner_id, root_id, conversation_id = await _seed_conversation(sessions)
    context = unit_context(owner_id)
    async with sessions() as db:
        root = await db.get(Agent, root_id)
        assert root is not None
        child = Agent(
            user_id=owner_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            name="Independent job researcher",
            system_prompt="Read the supplied source.",
            model_id=root.model_id,
            runtime_policy={"version": 1},
        )
        db.add(child)
        await db.flush()
        db.add(AgentMemorySettings(agent_id=child.id, memory_policy_override="off"))
        db.add(AgentSubAgentLink(parent_agent_id=root_id, sub_agent_id=child.id))
        await db.commit()
        accepted = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=conversation_id,
                root_agent_id=root_id,
                context=context,
                source="chat",
                input_preview="Compare two documents",
            ),
        )
        run = await db.get(ConversationRun, accepted.run_id)
        assert run is not None
        await transition_run(db, run, "running", worker_instance_id="parent-worker")
        await transition_run(db, run, "completed")
        await db.commit()
        return context, conversation_id, run.id, child.id


@pytest.mark.asyncio
async def test_duplicate_handoffs_after_rollback_allocate_only_one_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, engine = _postgres_session_factory()
    monkeypatch.setattr(settings, "independent_chat_jobs_enabled", True)
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    try:
        context, parent_id, run_id, target_id = await _accepted_parent(sessions)
        request = IndependentJobCreate(
            request_id=uuid4(), target_id=target_id, instruction="Read the second source."
        )

        # A process lost before COMMIT leaves no reusable job or runnable input.
        async with sessions() as db:
            parent = await db.get(Conversation, parent_id)
            assert parent is not None
            aborted = await accept_independent_job(db, parent, run_id, request, context)
            assert aborted.input_id is not None
            aborted_job_id = aborted.job.id
            await db.rollback()

        barrier = asyncio.Barrier(2)

        async def submit() -> tuple[UUID, UUID | None]:
            async with sessions() as db:
                parent = await db.get(Conversation, parent_id)
                assert parent is not None
                await barrier.wait()
                result = await accept_independent_job(db, parent, run_id, request, context)
                await db.commit()
                return result.job.id, result.input_id

        async with asyncio.timeout(15):
            first, second = await asyncio.gather(submit(), submit())

        assert first[0] == second[0] != aborted_job_id
        [input_id] = [item for _, item in (first, second) if item is not None]

        async with sessions() as db:
            parent = await db.get(Conversation, parent_id)
            assert parent is not None
            changed = request.model_copy(update={"instruction": "Different task."})
            with pytest.raises(HTTPException) as conflict:
                await accept_independent_job(db, parent, run_id, changed, context)
            assert conflict.value.status_code == 409
            await db.rollback()

        # Count every child of this target, including any orphaned by a rollback
        # or a losing request. The same original request is still recoverable.
        async with sessions() as db:
            job = (
                await db.scalars(
                    select(IndependentChatJob).where(IndependentChatJob.parent_run_id == run_id)
                )
            ).one()
            child = (
                await db.scalars(select(Conversation).where(Conversation.agent_id == target_id))
            ).one()
            child_run = (
                await db.scalars(
                    select(ConversationRun).where(ConversationRun.agent_id == target_id)
                )
            ).one()
            queued = (
                await db.scalars(
                    select(ConversationRunInput).where(ConversationRunInput.agent_id == target_id)
                )
            ).one()
            assert job.id == first[0] and job.request_id == request.request_id
            assert job.child_conversation_id == child.id == child_run.conversation_id
            assert job.child_run_id == queued.run_id == child_run.id
            assert queued.id == input_id and queued.status == "claimed"
            assert queued.input_payload["messages"] == [
                {"role": "user", "id": str(request.request_id), "content": request.instruction}
            ]
    finally:
        await engine.dispose()
