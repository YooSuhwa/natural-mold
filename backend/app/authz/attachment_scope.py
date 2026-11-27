"""An upload and any attached conversation must have the same private scope."""

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.message_attachment import MessageAttachment


def private_attachment_predicate(context: OrgContext) -> ColumnElement[bool]:
    return and_(
        private_scope_predicate(MessageAttachment, context),
        or_(
            MessageAttachment.conversation_id.is_(None),
            select(Conversation.id)
            .where(
                Conversation.id == MessageAttachment.conversation_id,
                private_scope_predicate(Conversation, context),
            )
            .exists(),
        ),
    )


async def load_private_attachment(
    db: AsyncSession, attachment_id: UUID, context: OrgContext
) -> MessageAttachment | None:
    """Load an upload, rechecking live delegation when its conversation is a child job."""
    result = await db.execute(
        select(MessageAttachment, Conversation.source)
        .outerjoin(Conversation, Conversation.id == MessageAttachment.conversation_id)
        .where(
            MessageAttachment.id == attachment_id,
            private_scope_predicate(MessageAttachment, context),
            or_(
                MessageAttachment.conversation_id.is_(None),
                private_scope_predicate(Conversation, context),
            ),
        )
        .execution_options(populate_existing=True, autoflush=False)
    )
    row = result.one_or_none()
    if row is None:
        return None
    attachment, source = row
    if source == INDEPENDENT_JOB_SOURCE and attachment.conversation_id is not None:
        from app.services.independent_job_runs import can_read_independent_child_conversation

        if not await can_read_independent_child_conversation(
            db, conversation_id=attachment.conversation_id, context=context
        ):
            return None
    return attachment
