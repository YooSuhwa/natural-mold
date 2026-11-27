"""Reauthorize an independent child against the parent's accepted and current delegation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.agent_runtime.identity import (
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.approvals.emergency import refuse_run, stop_for_agent
from app.authz.conversation_agent_checks import current_authz, require_agent_run
from app.authz.execution_lock_cursor import LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.authz.run_decisions import fresh_run_decision_scope
from app.authz.runtime_access_planner import RuntimeAccessPlanner
from app.authz.scoping import private_scope_predicate
from app.error_codes import conversation_not_found
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from app.services.chat.run_agents import get_run_agent_with_tools
from app.services.conversation_runtime_policy import snapshot_from_conversation
from app.services.readiness_gate import require_agent_ready
from app.tenancy.attribution import RunAttribution, attribute_child, attribute_run

if TYPE_CHECKING:
    from app.models.independent_chat_job import IndependentChatJob


def _same_identity(
    run: ConversationRun | ConversationRunAgent, attribution: RunAttribution
) -> bool:
    return (
        run.invoker_type == attribution.invoker_type
        and run.resource_owner_user_id == attribution.resource_owner_user_id
        and run.on_behalf_of_user_id == attribution.on_behalf_of_user_id
        and run.credential_subject_user_id == attribution.credential_subject_user_id
    )


async def _scoped_run(db: AsyncSession, run_id: UUID, context: OrgContext) -> ConversationRun:
    run = await db.scalar(
        select(ConversationRun)
        .join(Conversation, Conversation.id == ConversationRun.conversation_id)
        .where(
            ConversationRun.id == run_id,
            private_scope_predicate(ConversationRun, context),
            private_scope_predicate(Conversation, context),
            ConversationRun.agent_id == Conversation.agent_id,
            ConversationRun.run_source == "CHAT",
        )
        .options(noload(ConversationRun.metrics))
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise conversation_not_found()
    return run


def _check_snapshot(conversation: Conversation, run: ConversationRun) -> None:
    try:
        snapshot = snapshot_from_conversation(conversation)
    except RuntimePolicySnapshotError:
        snapshot = None
    if snapshot is None or (
        run.runtime_policy_version,
        run.runtime_policy_hash,
        run.runtime_policy_source,
    ) != (snapshot.effective.version, snapshot.policy_hash, snapshot.source):
        raise AppError(
            "independent_job_policy_changed",
            "작업에 적용된 실행 정책을 확인할 수 없습니다",
            status=409,
        )


async def _admitted_targets(
    db: AsyncSession, parent: ConversationRun, context: OrgContext, selected_agent_id: UUID | None
) -> list[Agent]:
    if parent.agent_id is None:
        raise conversation_not_found()
    root = await get_run_agent_with_tools(db, parent.agent_id, context)
    if root is None or root.archived_at is not None:
        raise conversation_not_found()
    accepted_root = attribute_run("chat", context, root)
    if not _same_identity(parent, accepted_root):
        raise conversation_not_found()
    identity = resolve_agent_run_identity(
        agent_id=root.id,
        agent_owner_user_id=root.user_id,
        runtime_name=root.runtime_name or make_agent_runtime_name(root.id),
        identity_mode=root.identity_mode,
        source=AgentRunSource.CHAT,
        caller_user_id=context.user_id,
    )
    await require_agent_run(context, root.id)
    plan = await RuntimeAccessPlanner(context, identity).plan(root)
    participants = {
        row.agent_id_snapshot: row
        for row in await db.scalars(
            select(ConversationRunAgent)
            .where(
                ConversationRunAgent.run_id == parent.id, ConversationRunAgent.role == "subagent"
            )
            .execution_options(populate_existing=True)
        )
    }
    targets: list[Agent] = []
    for link in plan.sub_agent_links:
        child = link.sub_agent
        if child is None or (selected_agent_id is not None and child.id != selected_agent_id):
            continue
        participant = participants.get(child.id)
        if (
            link.link_status != "active"
            or child.archived_at is not None
            or (child.org_id, child.tenant_id) != (context.org_id, context.tenant_id)
            or participant is None
            or participant.agent_id != child.id
            or participant.path != [str(root.id), str(child.id)]
            or not _same_identity(participant, attribute_child(accepted_root, child))
        ):
            continue
        # This is a separately usable conversation with the child as its root.
        # A parent's permission to invoke the child does not grant the caller that access.
        if child.user_id != context.user_id and not current_authz().enforces("agent"):
            continue
        try:
            await require_agent_run(context, child.id)
        except AppError as denied:
            if denied.status not in (403, 404):
                raise
            continue
        if await stop_for_agent(db, tenant_id=context.tenant_id, agent_id=child.id):
            continue
        # require_lease_supported cannot guard legacy agent-memory writes or
        # fallback-model sends. Hide those targets before a handoff is offered.
        if (
            child.runtime_profile != "standard"
            or child.model_fallback_list
            or child.runtime_policy is None
        ):
            if selected_agent_id == child.id:
                raise AppError(
                    "independent_job_unsupported_agent",
                    "이 에이전트의 실행 구성을 독립 작업에 사용할 수 없습니다",
                    status=409,
                )
            continue
        targets.append(child)
    return targets


async def _capability_digest(db: AsyncSession, targets: list[Agent], context: OrgContext) -> str:
    from app.authz.independent_job_capabilities import independent_capability_fingerprint

    if len(targets) != 1:
        raise conversation_not_found()
    return await independent_capability_fingerprint(db, targets[0], context)


async def _lock_targets(
    db: AsyncSession,
    parent_run_id: UUID,
    context: OrgContext,
    *,
    selected: UUID | None = None,
    job: IndependentChatJob | None = None,
) -> tuple[list[Agent], str | None]:
    parent = await _scoped_run(db, parent_run_id, context)
    conversations = [parent.conversation_id]
    if job is not None:
        if (parent.agent_id, parent.conversation_id) != (
            job.parent_agent_id,
            job.parent_conversation_id,
        ):
            raise conversation_not_found()
        conversations.append(job.child_conversation_id)
    with fresh_run_decision_scope() as memo:
        revision = None
        if current_authz().mode != "legacy":
            # During this preflight the transaction only reads. The second pass under
            # the gateway locks can reuse its decisions but cannot authorize a new grant.
            revision = await db.scalar(
                select(Organization.authz_epoch).where(Organization.id == context.org_id)
            )
            allowed = await _admitted_targets(db, parent, context, selected)
            if selected is not None:
                await _capability_digest(db, allowed, context)
                if job is None:
                    await require_agent_ready(db, context, selected)

            memo.seal()
        proof = await acquire_execution_proof(
            db, context, root_agent_id=parent.agent_id, conversation_ids=conversations
        )
        if proof.archived_bypass:
            raise conversation_not_found()
        if revision is not None and revision != await db.scalar(
            select(Organization.authz_epoch).where(Organization.id == context.org_id)
        ):
            raise AppError(
                "independent_job_authority_changed", "실행 권한이 변경되었습니다", status=409
            )
        parent = await _scoped_run(db, parent.id, context)
        origin = proof.cursor.held_row(LockClass.CONVERSATION, parent.conversation_id)
        if (
            not isinstance(origin, Conversation)
            or origin.source == INDEPENDENT_JOB_SOURCE
            or parent.agent_id is None
        ):
            raise conversation_not_found()
        _check_snapshot(origin, parent)
        await refuse_run(db, tenant_id=context.tenant_id, agent_id=parent.agent_id)
        targets = await _admitted_targets(db, parent, context, selected)
        digest = await _capability_digest(db, targets, context) if selected is not None else None
        if job is not None:
            child = await _scoped_run(db, job.child_run_id, context)
            destination = proof.cursor.held_row(LockClass.CONVERSATION, job.child_conversation_id)
            if (
                not isinstance(destination, Conversation)
                or destination.source != INDEPENDENT_JOB_SOURCE
                or child.conversation_id != job.child_conversation_id
                or child.agent_id != job.child_agent_id
                or not _same_identity(child, attribute_run("chat", context, targets[0]))
            ):
                raise conversation_not_found()
            _check_snapshot(destination, child)
            if digest != job.capability_sha256:
                raise AppError(
                    "independent_job_capabilities_changed",
                    "작업의 도구 또는 모델 사용 권한이 변경되었습니다",
                    status=409,
                )
        return targets, digest


async def list_independent_targets(
    db: AsyncSession, parent_run: ConversationRun, context: OrgContext
) -> list[Agent]:
    """Return only direct participants still delegable and runnable as an independent root."""
    targets, _ = await _lock_targets(db, parent_run.id, context)
    return targets


async def authorize_independent_target(
    db: AsyncSession, parent_run: ConversationRun, context: OrgContext, target_id: UUID
) -> tuple[Agent, str]:
    """Pin the current target's allowed capability set in the same transaction as acceptance."""
    targets, digest = await _lock_targets(db, parent_run.id, context, selected=target_id)
    if digest is None:
        raise conversation_not_found()
    return targets[0], digest


async def require_job_authority(
    db: AsyncSession, job: IndependentChatJob, context: OrgContext
) -> None:
    """Check the durable job afresh before dispatch; the caller releases locks before I/O."""
    from app.models.independent_chat_job import IndependentChatJob

    attached = await db.scalar(
        select(IndependentChatJob)
        .where(
            IndependentChatJob.id == job.id, private_scope_predicate(IndependentChatJob, context)
        )
        .execution_options(populate_existing=True)
    )
    if attached is None:
        raise conversation_not_found()
    await _lock_targets(
        db, attached.parent_run_id, context, selected=attached.child_agent_id, job=attached
    )
    # The job's cancellation is separate from the parent's normal finish or cancellation.
    active = await db.scalar(
        select(IndependentChatJob.id).where(
            IndependentChatJob.id == attached.id, IndependentChatJob.cancel_requested_at.is_(None)
        )
    )
    if active is None:
        raise AppError("independent_job_canceled", "중단된 독립 작업입니다", status=409)
