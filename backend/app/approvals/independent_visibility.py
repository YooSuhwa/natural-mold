"""Bind background approval metadata and reminders to their still-owned delegation."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.authz.conversation_agent_checks import current_authz
from app.authz.org_context import OrgContext, active_memberships
from app.authz.principal import Principal
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.approval_request import ApprovalRequest
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import Organization, Tenant
from app.models.user import User
from app.services.independent_job_runs import job_run_lineage


def _has_visible_job(context: OrgContext) -> ColumnElement[bool]:
    chain = job_run_lineage(context)
    child = aliased(Conversation)
    parent_agent, child_agent = aliased(Agent), aliased(Agent)
    membership = (
        active_memberships(context.user_id)
        .where(
            Organization.id == context.org_id,
            Organization.status == "active",
            Tenant.id == context.tenant_id,
            Tenant.status == "active",
        )
        .exists()
    )
    user_active = (
        select(User.id).where(User.id == context.user_id, User.is_active.is_(True)).exists()
    )
    return (
        select(chain.c.run_id)
        .join(
            IndependentChatJob,
            IndependentChatJob.id == chain.c.job_id,
        )
        .join(
            Conversation,
            Conversation.id == IndependentChatJob.parent_conversation_id,
        )
        .join(child, child.id == IndependentChatJob.child_conversation_id)
        .join(parent_agent, parent_agent.id == IndependentChatJob.parent_agent_id)
        .join(child_agent, child_agent.id == IndependentChatJob.child_agent_id)
        .join(
            AgentSubAgentLink,
            and_(
                AgentSubAgentLink.parent_agent_id == IndependentChatJob.parent_agent_id,
                AgentSubAgentLink.sub_agent_id == IndependentChatJob.child_agent_id,
            ),
        )
        .where(
            membership,
            user_active,
            AgentSubAgentLink.link_status == "active",
            private_scope_predicate(Conversation, context),
            Conversation.agent_id == IndependentChatJob.parent_agent_id,
            Conversation.agent_lifecycle_state == "active",
            child.agent_lifecycle_state == "active",
            child.agent_id == IndependentChatJob.child_agent_id,
            parent_agent.archived_at.is_(None),
            child_agent.archived_at.is_(None),
            IndependentChatJob.cancel_requested_at.is_(None),
            chain.c.run_id == ApprovalRequest.run_id,
            chain.c.conversation_id == ApprovalRequest.conversation_id,
            ApprovalRequest.org_id == context.org_id,
            ApprovalRequest.tenant_id == context.tenant_id,
            ApprovalRequest.subject_user_id == context.user_id,
            ApprovalRequest.decider_user_id == context.user_id,
        )
        .correlate(ApprovalRequest)
        .exists()
    )


async def readable_approval(db: AsyncSession, context: OrgContext) -> ColumnElement[bool]:
    independent = (
        select(Conversation.id)
        .where(
            Conversation.id == ApprovalRequest.conversation_id,
            Conversation.source == INDEPENDENT_JOB_SOURCE,
        )
        .correlate(ApprovalRequest)
        .exists()
    )
    if current_authz().enforces("agent"):
        try:
            confirmed = await authorized_independent_approval_ids(db, context, None)
        except AppError as exc:
            if exc.status < 500:
                raise
            return ~independent
        return or_(~independent, ApprovalRequest.id.in_(confirmed))
    return or_(~independent, _has_visible_job(context))


async def authorized_independent_approval_ids(
    db: AsyncSession,
    context: OrgContext,
    request_ids: Collection[UUID] | None,
) -> set[UUID]:
    if request_ids is not None and not request_ids:
        return set()
    query = (
        select(
            ApprovalRequest.id,
            IndependentChatJob.parent_agent_id,
            IndependentChatJob.child_agent_id,
        )
        .join(
            IndependentChatJob,
            IndependentChatJob.child_conversation_id == ApprovalRequest.conversation_id,
        )
        .where(_has_visible_job(context))
    )
    if request_ids is not None:
        query = query.where(ApprovalRequest.id.in_(request_ids))
    rows = (await db.execute(query)).all()
    authz = current_authz()
    if not rows or not authz.enforces("agent"):
        return {row.id for row in rows}
    objects = sorted(
        {f"agent:{agent}" for row in rows for agent in (row.parent_agent_id, row.child_agent_id)}
    )
    visible = set(
        await authz.filter_visible(Principal.for_user(context), "can_run", objects, legacy=[])
    )
    return {
        row.id
        for row in rows
        if f"agent:{row.parent_agent_id}" in visible and f"agent:{row.child_agent_id}" in visible
    }


async def independent_job_approval_is_current(
    db: AsyncSession,
    row: ApprovalRequest,
    context: OrgContext,
) -> bool:
    """Ordinary rows keep their own policy; an independent child must still be waiting."""
    if row.kind != "tool" or row.conversation_id is None:
        return True
    source = await db.scalar(
        select(Conversation.source).where(Conversation.id == row.conversation_id)
    )
    if source != INDEPENDENT_JOB_SOURCE:
        return source is not None
    if row.id not in await authorized_independent_approval_ids(db, context, [row.id]):
        return False
    if row.status != "pending":
        return True
    from app.services.independent_job_runs import current_job_run

    job = await db.scalar(
        select(IndependentChatJob).where(
            IndependentChatJob.child_conversation_id == row.conversation_id,
            private_scope_predicate(IndependentChatJob, context),
        )
    )
    if job is None:
        return False
    run = await current_job_run(db, job, context)
    return run is not None and run.id == row.run_id and run.status == "interrupted"
