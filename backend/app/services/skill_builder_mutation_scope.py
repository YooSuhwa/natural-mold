"""Explicit targets for private Skill Builder state mutations."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scope_predicate
from app.authz.skill_builder_scope import private_skill_builder_predicate
from app.error_codes import session_not_found
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.skill_builder_session import SkillBuilderSession
from app.storage.skill_draft_paths import skill_draft_storage_path


@dataclass(frozen=True, slots=True)
class SkillBuilderMutationTarget:
    session_id: UUID
    context: OrgContext


@dataclass(frozen=True, slots=True)
class SkillBuilderMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class SkillBuilderEvaluation:
    result: Mapping[str, JsonValue]
    draft: Mapping[str, JsonValue]


def require_session_workspace(session: SkillBuilderSession) -> str | None:
    """Only the canonical storage owned by this session can be read."""
    path = session.draft_workspace_path
    if path is not None and path != skill_draft_storage_path(session.id):
        raise session_not_found()
    return path


async def load_mutation_session(
    db: AsyncSession, target: SkillBuilderMutationTarget
) -> SkillBuilderSession:
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
    if session is None:
        raise session_not_found()
    return session


async def load_conversation_session(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> SkillBuilderSession | None:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    return await db.scalar(
        select(SkillBuilderSession)
        .join(Conversation, SkillBuilderSession.conversation_id == Conversation.id)
        .join(Agent, Conversation.agent_id == Agent.id)
        .where(
            SkillBuilderSession.conversation_id == conversation_id,
            private_skill_builder_predicate(context),
            private_scope_predicate(Conversation, context),
            private_scope_predicate(Agent, context),
        )
        .order_by(SkillBuilderSession.created_at.desc())
        .limit(1)
        .with_for_update(of=SkillBuilderSession)
        .execution_options(populate_existing=True, autoflush=False)
    )
