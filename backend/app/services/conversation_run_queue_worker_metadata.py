"""Identity-only detached queue targets authorize private payload hydration."""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, noload

from app.authz.conversation_input_scope import conversation_input_parent
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_parent_scoped, private_scope_predicate
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.user import User

RUN_IDENTITY_OPTIONS: Final = (
    load_only(
        ConversationRun.id,
        ConversationRun.conversation_id,
        ConversationRun.agent_id,
        ConversationRun.user_id,
        ConversationRun.status,
        raiseload=True,
    ),
    noload(ConversationRun.metrics),
)
RUN_TERMINAL_OPTIONS: Final = (
    load_only(
        ConversationRun.id,
        ConversationRun.conversation_id,
        ConversationRun.agent_id,
        ConversationRun.user_id,
        ConversationRun.status,
        ConversationRun.completed_at,
        ConversationRun.cancel_requested_at,
        raiseload=True,
    ),
    noload(ConversationRun.metrics),
)
INPUT_TERMINAL_OPTIONS: Final = load_only(
    ConversationRunInput.id,
    ConversationRunInput.conversation_id,
    ConversationRunInput.user_id,
    ConversationRunInput.agent_id,
    ConversationRunInput.run_id,
    ConversationRunInput.status,
    ConversationRunInput.revision,
    raiseload=True,
)


def queue_current_user(user: User) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        display_name=user.display_name,
        avatar_mode=user.avatar_mode,
        avatar_initials=user.avatar_initials,
        avatar_color=user.avatar_color,
        avatar_image_url=user.avatar_image_url,
        is_super_user=user.is_super_user,
    )


@dataclass(frozen=True, slots=True)
class QueueInputIdentity:
    id: UUID
    conversation_id: UUID
    agent_id: UUID
    user_id: UUID
    run_id: UUID | None
    status: str


@dataclass(frozen=True, slots=True)
class AuthorizedQueueTarget:
    run: ConversationRun
    context: OrgContext


class QueueBindingInvalidError(Exception):
    """The current Input/Run/Conversation/Agent tuple does not agree."""


def queue_binding_matches(
    queued: ConversationRunInput,
    run: ConversationRun,
    conversation: Row[tuple[UUID, UUID, UUID | None]],
) -> bool:
    return (
        queued.conversation_id == run.conversation_id == conversation[0]
        and queued.user_id == run.user_id == conversation[1]
        and queued.agent_id == run.agent_id == conversation[2]
    )


async def load_queue_input_identity(db: AsyncSession, input_id: UUID) -> QueueInputIdentity | None:
    row = (
        await db.execute(
            select(
                ConversationRunInput.id,
                ConversationRunInput.conversation_id,
                ConversationRunInput.agent_id,
                ConversationRunInput.user_id,
                ConversationRunInput.run_id,
                ConversationRunInput.status,
            )
            .where(ConversationRunInput.id == input_id)
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    return QueueInputIdentity(*row) if row is not None else None


async def authorize_queue_target(
    db: AsyncSession, identity: QueueInputIdentity, run: ConversationRun
) -> AuthorizedQueueTarget:
    parent = (
        await db.execute(
            select(Conversation.user_id, Conversation.org_id, Conversation.tenant_id)
            .select_from(ConversationRunInput)
            .join(Conversation, conversation_input_parent().on)
            .where(
                ConversationRunInput.id == identity.id,
                ConversationRunInput.conversation_id == identity.conversation_id,
                ConversationRunInput.agent_id == identity.agent_id,
                ConversationRunInput.run_id == run.id,
                ConversationRunInput.status == "claimed",
            )
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    if parent is None or parent[0] != identity.user_id:
        raise QueueBindingInvalidError
    resource = PrivateResourceActor(*parent)
    # This captured tuple only bounds identity SQL; execution authority is refreshed below.
    scope = OrgContext(
        org_id=resource.org_id, tenant_id=resource.tenant_id, user_id=resource.user_id
    )
    scoped_run_ids = (
        select(ConversationRun.id)
        .where(ConversationRun.id == run.id, private_run_predicate(scope))
        .correlate(None)
    )
    bound_id = await db.scalar(
        private_parent_scoped(
            select(ConversationRun.id)
            .select_from(ConversationRunInput)
            .join(ConversationRun, ConversationRun.id == ConversationRunInput.run_id)
            .where(
                ConversationRunInput.id == identity.id,
                ConversationRunInput.conversation_id == identity.conversation_id,
                ConversationRunInput.agent_id == identity.agent_id,
                ConversationRunInput.run_id == run.id,
                ConversationRunInput.status == "claimed",
                ConversationRun.id.in_(scoped_run_ids),
                ConversationRun.conversation_id == Conversation.id,
                ConversationRun.agent_id == Conversation.agent_id,
            ),
            conversation_input_parent(),
            scope,
        )
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(private_scope_predicate(Agent, scope))
    )
    if bound_id is None:
        raise QueueBindingInvalidError
    context = await resolve_private_actor(db, resource)
    return AuthorizedQueueTarget(run, context)
