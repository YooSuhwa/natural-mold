"""An artifact's root and conversation must agree on private organization scope."""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.conversation_scope import load_private_conversation, resolve_conversation_actor
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.error_codes import conversation_not_found
from app.models.conversation import Conversation
from app.models.conversation_artifact import ConversationArtifact


def private_artifact_predicate(context: OrgContext) -> ColumnElement[bool]:
    return and_(
        private_scope_predicate(ConversationArtifact, context),
        select(Conversation.id)
        .where(
            Conversation.id == ConversationArtifact.conversation_id,
            private_scope_predicate(Conversation, context),
        )
        .exists(),
    )


async def validated_artifact_conversation(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> Conversation:
    conversation = await load_private_conversation(db, conversation_id, context)
    if conversation is None:
        raise conversation_not_found()
    await resolve_conversation_actor(db, conversation_id, context.user_id)
    return conversation
