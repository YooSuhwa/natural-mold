"""Executable conversation policy and run preparation (Task 10 I4 split).

Moved unchanged from ``app.services.chat.conversations``, which re-exports every name.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import (
    SKILL_BUILDER_RUNTIME_POLICY,
    RuntimePolicySnapshotError,
    runtime_policy_to_json,
)
from app.authz.agent_execution_scope import (
    AgentExecutionTarget,
    load_agent_tree_for_execution,
)
from app.authz.conversation_agent_checks import require_agent_run
from app.authz.conversation_scope import lock_private_conversation
from app.authz.error_codes import agent_archived
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.error_codes import conversation_not_found
from app.exceptions import AppError, ConflictError
from app.models.agent import AGENT_RUNTIME_PROFILE_SKILL_BUILDER, Agent
from app.models.conversation import Conversation
from app.schemas.conversation_scope import ConversationPolicyTarget, ConversationPromotion
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy


def conversation_title_from_content(content: str) -> str:
    title = content.strip().replace("\n", " ")
    if not title:
        return "새 대화"
    if len(title) > 40:
        return title[:37] + "..."
    return title


async def create_conversation(
    db: AsyncSession,
    agent_id: uuid.UUID,
    title: str | None = None,
    *,
    context: OrgContext,
    source: str = "ui",
) -> Conversation:
    executable = await load_agent_tree_for_execution(db, AgentExecutionTarget(agent_id, context))
    agent = next(item for item in executable.agents if item.id == agent_id)
    conv = Conversation(
        agent_id=agent_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
        title=title or "새 대화",
        source=source,
    )
    if source == "draft":
        runtime_profile = await db.scalar(select(Agent.runtime_profile).where(Agent.id == agent_id))
        if runtime_profile == AGENT_RUNTIME_PROFILE_SKILL_BUILDER:
            conv.runtime_policy_snapshot = runtime_policy_to_json(
                SKILL_BUILDER_RUNTIME_POLICY.effective
            )
            conv.runtime_policy_version = SKILL_BUILDER_RUNTIME_POLICY.effective.version
            conv.runtime_policy_hash = SKILL_BUILDER_RUNTIME_POLICY.policy_hash
            conv.runtime_policy_source = SKILL_BUILDER_RUNTIME_POLICY.source
    db.add(conv)
    await db.flush()
    await db.refresh(conv)
    return conv


async def require_executable_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> uuid.UUID:
    """Acquire the execution proof and assert Agent lifecycle before private hydration.

    The canonical gateway locks Tenant -> Organization -> owner memberships ->
    User -> Agent lifecycle -> this Conversation once per root transaction; a
    repeated call in the same transaction reuses that proof without lock SQL.
    """
    try:
        await require_private_actor(db, context)
    except AppError as exc:
        raise conversation_not_found() from exc
    row = (
        await db.execute(
            select(Conversation.agent_id, Conversation.agent_lifecycle_state)
            .where(
                Conversation.id == conversation_id,
                private_scope_predicate(Conversation, context),
            )
            .execution_options(populate_existing=True, autoflush=False)
        )
    ).one_or_none()
    if row is None:
        raise conversation_not_found()
    if row.agent_id is None or row.agent_lifecycle_state == "purged":
        raise agent_archived()
    await acquire_execution_proof(
        db, context, root_agent_id=row.agent_id, conversation_ids=(conversation_id,)
    )
    await load_agent_tree_for_execution(db, AgentExecutionTarget(row.agent_id, context))
    return row.agent_id


async def prepare_executable_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    context: OrgContext,
    *,
    agent_checked: bool = False,
) -> uuid.UUID:
    """Initialize policy, then reacquire lifecycle authority for config hydration.

    The single ``agent#can_run`` decision of a chat run, unless the caller made it
    before creating the conversation (``agent_checked``).
    """
    agent_id = await require_executable_conversation(db, conversation_id, context)
    if not agent_checked:
        await require_agent_run(context, agent_id)
    try:
        await ensure_conversation_runtime_policy(
            db, ConversationPolicyTarget(conversation_id, context)
        )
    except RuntimePolicySnapshotError as exc:
        raise ConflictError(exc.code, exc.code) from None
    await db.commit()
    return await require_executable_conversation(db, conversation_id, context)


async def promote_draft_conversation(
    db: AsyncSession, target: ConversationPromotion
) -> Conversation:
    try:
        await require_private_actor(db, target.context)
    except AppError as exc:
        raise conversation_not_found() from exc
    agent_id = await db.scalar(
        select(Conversation.agent_id)
        .where(
            Conversation.id == target.conversation_id,
            private_scope_predicate(Conversation, target.context),
        )
        .execution_options(autoflush=False)
    )
    if agent_id is not None:
        await require_executable_conversation(db, target.conversation_id, target.context)
    conv = await lock_private_conversation(db, target.conversation_id, target.context)
    if conv.source != "draft":
        return conv
    conv.source = "ui"
    if target.title_from_content:
        conv.title = conversation_title_from_content(target.title_from_content)
    await db.flush()
    await db.refresh(conv)
    return conv
