"""A duplicate handoff racing on two PostgreSQL connections owns a single execution."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent_runtime.runtime import memory_context
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.independent_job_execution import authorize_independent_job_execution
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import ShieldedAsyncSession, tenant_session
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.independent_chat_job import IndependentChatJob
from app.models.memory import AgentMemorySettings, MemoryRecall, MemoryRecallRun, MemoryRecord
from app.models.model import Model
from app.schemas.independent_chat_job import IndependentJobCreate
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_run_service import transition_run
from app.services.independent_jobs import accept_independent_job
from app.services.prepared_run_adapter import (
    PreparedRunRequest,
    PreparedRunResolver,
    chat_config_resolver,
)
from app.text import korean_terms
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.authz.prepared_adapter_testkit import patch_keys
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.independent_recall_controls import (
    install_recall_probes,
    verify_ordinary_preparation,
)
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


@pytest.fixture
def recall_lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@pytest.mark.asyncio
async def test_preparing_independent_job_twice_recalls_locally_before_marker(
    recall_lab: RlsLab, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    admin = create_async_engine(async_url(recall_lab.admin_url))
    seed_sessions = async_sessionmaker(admin, expire_on_commit=False)
    app = create_async_engine(async_url(recall_lab.app_url))
    sessions = async_sessionmaker(app, class_=ShieldedAsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(settings, "independent_chat_jobs_enabled", True)
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    monkeypatch.setattr(
        memory_context,
        "recall_session",
        lambda ctx: tenant_session(ctx, maker=sessions),
    )
    patch_keys(monkeypatch)
    probes = install_recall_probes(monkeypatch, sessions)
    try:
        context, parent_id, run_id, target_id = await _accepted_parent(seed_sessions)
        query = "세션 만료 시간 알려줘"
        fact = "세션 만료 시간은 30분이다"
        async with seed_sessions() as db:
            target_agent = await db.get(Agent, target_id)
            assert target_agent is not None
            model = await db.get(Model, target_agent.model_id)
            assert model is not None
            model.provider = "anthropic"
            record = MemoryRecord(
                tenant_id=context.tenant_id,
                org_id=context.org_id,
                user_id=context.user_id,
                scope="user",
                content=fact,
                store_path="user/session-expiry",
                origin="user_stated",
                approved_by=context.user_id,
                approval_channel="proposal",
                approved_at=datetime.now(UTC).replace(tzinfo=None),
                search_tokens_analyzer=korean_terms.NORI_ANALYZER,
                search_tokens="remote-index-token",
            )
            revoked = MemoryRecord(
                tenant_id=context.tenant_id,
                org_id=context.org_id,
                user_id=context.user_id,
                scope="user",
                content="세션 만료 시간은 40분이다",
                store_path="user/old-session-expiry",
                status="revoked",
            )
            db.add_all([record, revoked])
            await db.commit()
        async with tenant_session(context, maker=sessions) as db:
            parent = await db.get(Conversation, parent_id)
            assert parent is not None
            accepted = await accept_independent_job(
                db,
                parent,
                run_id,
                IndependentJobCreate(request_id=uuid4(), target_id=target_id, instruction=query),
                context,
            )
            assert accepted.input_id is not None
            claimed = await db.get(ConversationRunInput, accepted.input_id)
            assert claimed is not None and claimed.status == "claimed"
            child = await db.get(Conversation, accepted.job.child_conversation_id)
            assert child is not None and child.source == "independent_job"
            child_run_id = accepted.job.child_run_id
            assert child_run_id is not None and claimed.run_id == child_run_id
            await db.commit()

        execution = RunExecutionTarget(
            child_run_id, RunConversationTarget(child.id, target_id, context)
        )
        user = CurrentUser(id=context.user_id, email="owner@test.invalid", name="Owner")
        audit = select(MemoryRecallRun).where(MemoryRecallRun.run_id == str(child_run_id))
        recalls = select(MemoryRecall.record_id).where(MemoryRecall.run_id == str(child_run_id))
        count = select(MemoryRecord.recall_count).where(MemoryRecord.id == record.id)
        for commit in (False, True):
            # Start with fresh sessions/configs as a restarted worker would after a crash.
            async with (
                tenant_session(context, maker=sessions) as owner,
                tenant_session(context, maker=sessions) as observer,
            ):
                assert await authorize_independent_job_execution(
                    owner, run_id=child_run_id, conversation_id=child.id, context=context
                )
                locked = await lock_executable_run(owner, execution)
                assert locked is not None
                resolver = PreparedRunResolver(
                    PreparedRunRequest(context, chat_config_resolver(user, context), tmp_path)
                )
                async with asyncio.timeout(10):
                    await resolver(owner, locked)
                cfg = resolver.execution_cfg
                assert cfg is not None and cfg.independent_job
                assert fact in (cfg.memory_recall_block or "")
                assert [brief["id"] for brief in cfg.recalled_memories or []] == [str(record.id)]
                probes.assert_unused()
                assert await observer.scalar(audit) is None
                assert await observer.scalar(count) == 0
                if commit:
                    await owner.commit()
                else:
                    await owner.rollback()
                assert await observer.scalar(count) == int(commit)
                assert list(await observer.scalars(recalls)) == ([record.id] if commit else [])

        async with tenant_session(context, maker=sessions) as db:
            receipt = (await db.scalars(audit)).one()
            assert (receipt.mode, receipt.fallback) == ("fresh", "independent_preparation_keyword")
            assert (receipt.conversation_id, receipt.agent_id, receipt.user_id) == (
                child.id,
                target_id,
                context.user_id,
            )
            assert (
                await db.scalar(
                    select(MemoryRecord.recall_count).where(MemoryRecord.id == revoked.id)
                )
                == 0
            )

        # Even knowing the exact child run ID cannot expose the receipt in another company.
        other_company = replace(context, tenant_id=recall_lab.tenant_b)
        async with tenant_session(other_company, maker=sessions) as outsider:
            assert await outsider.scalar(audit) is None
            assert await outsider.scalar(recalls) is None

        # A normal chat with the same target and probes can use both remote services.
        probes.assert_unused()
        await verify_ordinary_preparation(sessions, context, target_id, tmp_path, probes)
    finally:
        await app.dispose()
        await admin.dispose()
