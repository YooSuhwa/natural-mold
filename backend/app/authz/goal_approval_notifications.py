"""The bell exposes a goal's approval only while its private conversation is accessible."""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.approval_request import ApprovalRequest
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_goal import ConversationGoal
from app.models.notification import Notification


def is_goal_approval(row: Notification) -> bool:
    return (
        row.kind == "approval.requested"
        and row.target_type == "approval_request"
        and (row.payload or {}).get("chat_goal") is True
    )


async def accessible_goal_approvals(
    db: AsyncSession, ctx: OrgContext, notice_ids: Collection[UUID]
) -> set[str]:
    if not notice_ids:
        return set()
    try:
        await require_private_actor(db, ctx)
    except AppError as exc:
        if exc.status not in {403, 404, 409}:
            raise
        return set()
    ref = ChatNotificationOutbox
    request = ApprovalRequest
    ids = await db.scalars(
        select(request.id)
        .select_from(ref)
        .join(Notification, Notification.id == ref.notification_id)
        .join(request, request.id == ref.approval_request_id)
        .join(Conversation, Conversation.id == ref.conversation_id)
        .join(ConversationGoal, ConversationGoal.id == ref.goal_id)
        .where(
            ref.notification_id.in_(notice_ids),
            ref.kind == "chat_goal.approval_requested",
            ref.user_id == ctx.user_id,
            ref.org_id == ctx.org_id,
            ref.tenant_id == ctx.tenant_id,
            Notification.target_id == request.id,
            request.run_id == ref.run_id,
            request.conversation_id == ref.conversation_id,
            request.subject_user_id == ctx.user_id,
            request.decider_user_id == ctx.user_id,
            request.org_id == ctx.org_id,
            request.tenant_id == ctx.tenant_id,
            private_scope_predicate(Conversation, ctx),
            private_scope_predicate(ConversationGoal, ctx),
            ConversationGoal.conversation_id == ref.conversation_id,
            ConversationGoal.version == ref.goal_version,
        )
    )
    return {f"approval_request:{item}" for item in ids}
