"""Accept one independently owned child run and its durable launch input atomically."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import AgentRunSource
from app.authz.independent_job_authority import (
    authorize_independent_target,
    list_independent_targets,
)
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.config import settings
from app.error_codes import conversation_not_found, resume_not_found
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.schemas.independent_chat_job import (
    IndependentJobCreate,
    IndependentJobsOut,
    IndependentTargetOut,
)
from app.services import chat_service, conversation_run_service
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_run_queue_persistence import persist_direct_input
from app.services.independent_job_runs import (
    can_read_independent_child_conversation,
    describe_readable_jobs,
)
from app.services.prepared_run_decisions import predecided_run


@dataclass(frozen=True, slots=True)
class AcceptedJob:
    job: IndependentChatJob
    input_id: UUID | None


def independent_jobs_available() -> bool:
    return settings.independent_chat_jobs_enabled and settings.chat_work_notifications_enabled


async def owned_run(
    db: AsyncSession,
    conversation_id: UUID,
    run_id: UUID,
    context: OrgContext,
    *,
    lock: bool = False,
) -> ConversationRun:
    run = await conversation_run_service.get_run_for_user(
        db,
        conversation_id=conversation_id,
        run_id=run_id,
        context=context,
        for_update=lock,
    )
    if run is None or run.tenant_id != context.tenant_id or run.org_id != context.org_id:
        raise resume_not_found()
    return run


async def accept_independent_job(
    db: AsyncSession,
    parent: Conversation,
    run_id: UUID,
    data: IndependentJobCreate,
    context: OrgContext,
) -> AcceptedJob:
    if parent.source == "independent_job":
        raise HTTPException(409, "Independent jobs cannot delegate another independent job")
    if not parent.agent_id or parent.source not in {"ui", "draft"} or parent.side_chat_parent_id:
        raise conversation_not_found()
    run = await owned_run(db, parent.id, run_id, context)
    if run.agent_id != parent.agent_id or run.run_source != "CHAT":
        raise resume_not_found()
    replay = await _find_replayed_job(db, run.id, data, context)
    if replay is not None:
        if not await can_read_independent_child_conversation(
            db, conversation_id=replay.child_conversation_id, context=context
        ):
            raise resume_not_found()
        return AcceptedJob(replay, None)
    if not independent_jobs_available():
        raise HTTPException(409, "Independent jobs are disabled")
    async with predecided_run(
        context, data.target_id, source=AgentRunSource.CHAT, caller_user_id=context.user_id
    ):
        return await _accept_authorized_job(db, parent, run, data, context)


async def _accept_authorized_job(
    db: AsyncSession,
    parent: Conversation,
    run: ConversationRun,
    data: IndependentJobCreate,
    context: OrgContext,
) -> AcceptedJob:
    # Authorize before acquiring row locks, then recheck under the parent proof.
    # Concurrent requests may have won the same acceptance in the meantime.
    _, capability_sha256 = await authorize_independent_target(db, run, context, data.target_id)
    run = await owned_run(db, parent.id, run.id, context, lock=True)
    await db.refresh(run, ["status"])
    replay = await _find_replayed_job(db, run.id, data, context)
    if replay is not None:
        return AcceptedJob(replay, None)
    if run.status not in {"running", "completed", "interrupted"}:
        raise HTTPException(409, "Parent run cannot start an independent job")
    if data.source_child_task_id is not None:
        from app.services.independent_job_source import require_source_task

        await require_source_task(db, data.source_child_task_id, run, data.target_id, context)
    child = await chat_service.create_conversation(
        db,
        data.target_id,
        chat_service.conversation_title_from_content(data.instruction),
        context=context,
        source="independent_job",
    )
    job_id = uuid4()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=child.id,
            root_agent_id=data.target_id,
            context=context,
            source="chat",
            run_source="start",
            input_preview=data.instruction,
        ),
    )
    child_run = await db.get(ConversationRun, accepted.run_id)
    if child_run is None:
        raise RuntimeError("Newly accepted child run disappeared")
    job = IndependentChatJob(
        id=job_id,
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        parent_conversation_id=parent.id,
        parent_run_id=run.id,
        parent_agent_id=parent.agent_id,
        child_agent_id=data.target_id,
        child_conversation_id=child.id,
        child_run_id=child_run.id,
        request_id=data.request_id,
        source_child_task_id=data.source_child_task_id,
        request_digest=_request_digest(data),
        capability_sha256=capability_sha256,
    )
    db.add(job)
    queued = await persist_direct_input(
        db,
        run=child_run,
        context=context,
        client_request_id=str(job_id),
        attachment_ids=[],
        input_payload={
            "messages": [{"role": "user", "content": data.instruction, "id": str(data.request_id)}]
        },
    )
    return AcceptedJob(job, queued.id)


def _request_digest(data: IndependentJobCreate) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                str(data.target_id),
                data.instruction,
                str(data.source_child_task_id) if data.source_child_task_id else None,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def _find_replayed_job(
    db: AsyncSession, run_id: UUID, data: IndependentJobCreate, context: OrgContext
) -> IndependentChatJob | None:
    keys = [IndependentChatJob.request_id == data.request_id]
    if data.source_child_task_id is not None:
        keys.append(IndependentChatJob.source_child_task_id == data.source_child_task_id)
    existing = (
        await db.scalars(
            select(IndependentChatJob).where(
                private_scope_predicate(IndependentChatJob, context),
                IndependentChatJob.parent_run_id == run_id,
                or_(*keys),
            )
        )
    ).all()
    if existing:
        if len(existing) != 1 or existing[0].request_digest != _request_digest(data):
            raise HTTPException(409, "Request identifier already belongs to different work")
        return existing[0]

    return None


async def list_independent_jobs(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    run_id: UUID | None,
) -> IndependentJobsOut:
    jobs = await db.scalars(
        select(IndependentChatJob)
        .where(
            private_scope_predicate(IndependentChatJob, context),
            or_(
                IndependentChatJob.parent_conversation_id == conversation.id,
                IndependentChatJob.child_conversation_id == conversation.id,
            ),
        )
        .order_by(IndependentChatJob.created_at.desc(), IndependentChatJob.id)
    )
    result = IndependentJobsOut(enabled=independent_jobs_available())
    for view in await describe_readable_jobs(db, jobs.all(), context):
        if view.child_conversation_id == conversation.id:
            result.parent_job = view
        else:
            result.jobs.append(view)
    if (
        run_id
        and result.enabled
        and conversation.source in {"ui", "draft"}
        and not conversation.side_chat_parent_id
    ):
        run = await owned_run(db, conversation.id, run_id, context)
        if run.status in {"running", "completed", "interrupted"} and run.run_source == "CHAT":
            result.targets = [
                IndependentTargetOut(id=a.id, name=a.name)
                for a in await list_independent_targets(db, run, context)
            ]
    return result
