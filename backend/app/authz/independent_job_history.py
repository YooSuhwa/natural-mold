"""Read a delegated chat's history using live ownership, delegation, and actor grants."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select

from app.agent_runtime.identity import AGENT_IDENTITY_FIXED, AGENT_IDENTITY_PER_USER
from app.authz.conversation_agent_checks import current_authz
from app.authz.org_context import OrgContext, active_memberships
from app.authz.principal import Principal
from app.authz.scoping import private_scope_predicate
from app.authz.service import AuthzService
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import Organization, Tenant
from app.models.user import User


def job_history_candidates(
    context: OrgContext,
    authz: AuthzService,
) -> Select[tuple[UUID, UUID, UUID, UUID, str, UUID, UUID, str]]:
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
    query = (
        select(
            IndependentChatJob.id.label("job_id"),
            child.id.label("conversation_id"),
            parent_agent.id.label("parent_agent_id"),
            child_agent.id.label("child_agent_id"),
            parent_agent.identity_mode,
            parent_agent.user_id.label("parent_owner"),
            child_agent.user_id.label("child_owner"),
            child_agent.identity_mode.label("child_identity"),
        )
        .select_from(IndependentChatJob)
        .join(Conversation, Conversation.id == IndependentChatJob.parent_conversation_id)
        .join(ConversationRun, ConversationRun.id == IndependentChatJob.parent_run_id)
        .join(child, child.id == IndependentChatJob.child_conversation_id)
        .join(parent_agent, parent_agent.id == IndependentChatJob.parent_agent_id)
        .join(child_agent, child_agent.id == IndependentChatJob.child_agent_id)
        .join(
            AgentSubAgentLink,
            and_(
                AgentSubAgentLink.parent_agent_id == parent_agent.id,
                AgentSubAgentLink.sub_agent_id == child_agent.id,
            ),
        )
        .where(
            membership,
            user_active,
            private_scope_predicate(IndependentChatJob, context),
            private_scope_predicate(Conversation, context),
            private_scope_predicate(child, context),
            private_scope_predicate(ConversationRun, context),
            ConversationRun.conversation_id == Conversation.id,
            ConversationRun.agent_id == parent_agent.id,
            ConversationRun.run_source == "CHAT",
            Conversation.agent_id == parent_agent.id,
            Conversation.agent_lifecycle_state == "active",
            Conversation.source != INDEPENDENT_JOB_SOURCE,
            child.agent_id == child_agent.id,
            child.agent_lifecycle_state == "active",
            child.source == INDEPENDENT_JOB_SOURCE,
            AgentSubAgentLink.link_status == "active",
            parent_agent.archived_at.is_(None),
            child_agent.archived_at.is_(None),
            parent_agent.org_id == context.org_id,
            parent_agent.tenant_id == context.tenant_id,
            child_agent.org_id == context.org_id,
            child_agent.tenant_id == context.tenant_id,
            parent_agent.identity_mode.in_([AGENT_IDENTITY_FIXED, AGENT_IDENTITY_PER_USER]),
        )
        .execution_options(autoflush=False)
    )
    if not authz.enforces("agent"):
        query = query.where(
            parent_agent.user_id == context.user_id, child_agent.user_id == context.user_id
        )
    return query


async def readable_job_conversations(
    db: AsyncSession,
    context: OrgContext,
    *,
    job_ids: Collection[UUID] | None = None,
    conversation_ids: Collection[UUID] | None = None,
    authz: AuthzService | None = None,
    propagate_outage: bool = False,
) -> dict[UUID, UUID]:
    """Return scoped job→child pairs. A canceled or completed job still has readable history."""
    if job_ids is None and conversation_ids is None:
        raise ValueError("At least one bounded set of jobs or conversations is required")
    if job_ids is not None and not job_ids or conversation_ids is not None and not conversation_ids:
        return {}
    engine = authz or current_authz()
    candidates = (
        job_history_candidates(context, engine)
        .add_columns(
            IndependentChatJob.parent_conversation_id,
            IndependentChatJob.parent_run_id,
            IndependentChatJob.child_run_id,
            AgentSubAgentLink.created_at.label("link_created_at"),
            AgentSubAgentLink.delegated_by,
            select(Organization.authz_epoch)
            .where(Organization.id == context.org_id)
            .scalar_subquery()
            .label("authz_epoch"),
        )
        .subquery()
    )
    query = select(candidates).execution_options(autoflush=False)
    if job_ids is not None:
        query = query.where(candidates.c.job_id.in_(job_ids))
    if conversation_ids is not None:
        query = query.where(candidates.c.conversation_id.in_(conversation_ids))
    rows = (await db.execute(query)).all()
    if not rows or not engine.enforces("agent"):
        return {r.job_id: r.conversation_id for r in rows}

    try:
        requested = sorted(
            {f"agent:{agent}" for r in rows for agent in (r.parent_agent_id, r.child_agent_id)}
        )
        allowed = set(
            await engine.filter_visible(
                Principal.for_user(context),
                "can_run",
                requested,
                legacy=[],
                consistency="HIGHER_CONSISTENCY",
            )
        )
        eligible = [
            r
            for r in rows
            if f"agent:{r.parent_agent_id}" in allowed and f"agent:{r.child_agent_id}" in allowed
        ]
        # A fixed parent's authority to delegate a different account's child is
        # separate from the chat user's ability to open both agents directly.
        delegated: dict[UUID, set[str]] = defaultdict(set)
        for row in eligible:
            if row.identity_mode == AGENT_IDENTITY_FIXED and row.child_owner != row.parent_owner:
                delegated[row.parent_agent_id].add(f"agent:{row.child_agent_id}")
        for parent_id, targets in delegated.items():
            principal = Principal(
                "agent", parent_id, context.org_id, "CHAT", acting_user_id=context.user_id
            )
            delegated[parent_id] = set(
                await engine.filter_visible(
                    principal,
                    "can_run",
                    sorted(targets),
                    legacy=[],
                    consistency="HIGHER_CONSISTENCY",
                )
            )
        authorized = {
            r
            for r in eligible
            if (
                r.identity_mode != AGENT_IDENTITY_FIXED
                or r.child_owner == r.parent_owner
                or f"agent:{r.child_agent_id}" in delegated[r.parent_agent_id]
            )
        }
    except AppError:
        if propagate_outage:
            raise
        return {}

    if not authorized:
        return {}
    # The local membership, origin, delegation or organization policy can change
    # while checking OpenFGA. Only return rows proved against the same bindings.
    current = await db.execute(query.where(candidates.c.job_id.in_([r.job_id for r in authorized])))
    return {r.job_id: r.conversation_id for r in current if r in authorized}
