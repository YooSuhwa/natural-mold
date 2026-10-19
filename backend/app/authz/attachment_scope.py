"""An upload and any attached conversation must have the same private scope."""

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation import Conversation
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
