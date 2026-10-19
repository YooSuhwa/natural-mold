"""Detached skill-builder work restores scope from the persisted session target."""

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder_scope import SkillBuilderToolTarget
from app.storage.skill_draft_paths import skill_draft_storage_path


def private_skill_builder_predicate(context: OrgContext) -> ColumnElement[bool]:
    """An attached session can only reference its private conversation and agent."""
    agent = (
        select(Agent.id)
        .where(Agent.id == Conversation.agent_id, private_scope_predicate(Agent, context))
        .correlate(Conversation)
        .exists()
    )
    conversation = (
        select(Conversation.id)
        .where(
            Conversation.id == SkillBuilderSession.conversation_id,
            private_scope_predicate(Conversation, context),
            or_(Conversation.agent_id.is_(None), agent),
        )
        .correlate(SkillBuilderSession)
        .exists()
    )
    return and_(
        private_scope_predicate(SkillBuilderSession, context),
        or_(SkillBuilderSession.conversation_id.is_(None), conversation),
        or_(
            SkillBuilderSession.source_skill_id.is_(None),
            select(Skill.id)
            .where(
                Skill.id == SkillBuilderSession.source_skill_id,
                private_scope_predicate(Skill, context),
            )
            .correlate(SkillBuilderSession)
            .exists(),
        ),
        or_(
            SkillBuilderSession.finalized_skill_id.is_(None),
            select(Skill.id)
            .where(
                Skill.id == SkillBuilderSession.finalized_skill_id,
                private_scope_predicate(Skill, context),
            )
            .correlate(SkillBuilderSession)
            .exists(),
        ),
    )


async def resolve_skill_builder_actor(
    db: AsyncSession, session_id: UUID, user_id: UUID
) -> OrgContext:
    session = (
        await db.execute(
            select(
                SkillBuilderSession.user_id,
                SkillBuilderSession.org_id,
                SkillBuilderSession.tenant_id,
            )
            .where(SkillBuilderSession.id == session_id, SkillBuilderSession.user_id == user_id)
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    if session is None:
        raise AppError("SESSION_NOT_FOUND", "builder session not found", 404)
    return await resolve_private_actor(db, PrivateResourceActor(session[0], session[1], session[2]))


async def require_skill_builder_tool_session(
    db: AsyncSession, target: SkillBuilderToolTarget
) -> SkillBuilderSession:
    """Validate current authority, exact session and workspace before file effects."""
    context = target.context
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    session = await db.scalar(
        select(SkillBuilderSession)
        .where(
            SkillBuilderSession.id == target.session_id,
            private_skill_builder_predicate(context),
        )
        .with_for_update()
        .execution_options(populate_existing=True, autoflush=False)
    )
    if (
        session is None
        or session.draft_workspace_path != target.workspace_path
        or target.workspace_path != skill_draft_storage_path(session.id)
    ):
        raise AppError("SESSION_NOT_FOUND", "builder session not found", 404)
    if target.agent_id is not None:
        agent_id = await db.scalar(
            select(Agent.id)
            .where(Agent.id == target.agent_id, private_scope_predicate(Agent, context))
            .execution_options(autoflush=False)
        )
        if agent_id is None:
            raise AppError("SESSION_NOT_FOUND", "builder session not found", 404)
    if session.conversation_id is not None:
        conversation = await db.scalar(
            select(Conversation)
            .join(Agent, Conversation.agent_id == Agent.id)
            .where(
                Conversation.id == session.conversation_id,
                private_scope_predicate(Conversation, context),
                private_scope_predicate(Agent, context),
            )
            .execution_options(populate_existing=True, autoflush=False)
        )
        if conversation is None or (
            target.agent_id is not None and conversation.agent_id != target.agent_id
        ):
            raise AppError("SESSION_NOT_FOUND", "builder session not found", 404)
    return session
