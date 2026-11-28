"""Revalidate the authenticated actor and exact chat target for each journal read."""

from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.authz.conversation_scope import can_read_conversation_run_id, require_private_conversation
from app.authz.org_context import OrgContext
from app.dependencies import get_current_user
from app.error_codes import conversation_not_found, resume_not_found
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.child_task_read_bindings import readable_child_task_visibility
from app.services.conversation_run_service import get_run_for_user


async def require_read_conversation(
    db: AsyncSession, *, request: Request, context: OrgContext, conversation_id: UUID
) -> Conversation:
    """Recheck the original HTTP credential and pinned organization in a fresh session."""
    with db.no_autoflush:
        user = await get_current_user(request, db)
        if user.id != context.user_id or user.is_super_user != context.is_super_user:
            raise conversation_not_found()
        return await require_private_conversation(db, conversation_id, context)


async def require_read_run(
    db: AsyncSession, *, conversation: Conversation, context: OrgContext, run_id: UUID
) -> ConversationRun:
    """Recheck the private run and the exact resume lineage of a delegated chat."""
    with db.no_autoflush:
        if not await can_read_conversation_run_id(db, conversation, run_id, context):
            raise resume_not_found()
        try:
            run = await get_run_for_user(
                db, conversation_id=conversation.id, run_id=run_id, context=context
            )
        except AppError as exc:
            if exc.status not in (403, 404):
                raise
            raise resume_not_found() from None
        if run is None:
            raise resume_not_found()
        return run


async def load_child_visibility(
    db: AsyncSession, conversation: Conversation, context: OrgContext, run_id: UUID
) -> ChildVisibility:
    with db.no_autoflush:
        return await readable_child_task_visibility(db, conversation, context, run_id)
