"""Deliver a waiting goal through its ordinary, still-actionable approval request."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.conversation_checkpointer import conversation_checkpointer, deferred_saver
from app.agent_runtime.langgraph_pending_inputs import interrupt_payloads_from_checkpointer
from app.approvals import recording, service
from app.authz.notification_delivery_scope import lock_active_notice_recipient
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.config import settings
from app.exceptions import AppError
from app.models.approval_request import ApprovalRequest
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_goal import ConversationGoal, GoalTransitionIntent
from app.models.conversation_run import ConversationRun
from app.models.notification import Notification
from app.services.conversation_run_service import get_latest_interrupted_run


class ApprovalNotReady(Exception):
    """The checkpoint or original worker can still provide this pending approval."""


async def _recover_request(
    db: AsyncSession, run: ConversationRun, ctx: OrgContext, name: str, interrupt_id: str
) -> ApprovalRequest | None:
    stopped_at = service.aware(run.completed_at or run.updated_at)
    expires_at = stopped_at + timedelta(minutes=settings.approval_ttl_minutes)
    now = service.now()
    if now >= expires_at:
        return None
    if now < stopped_at + timedelta(seconds=5):
        # Give the warm producer, which still has the run's pinned redaction context,
        # its first chance to write the normal masked action card.
        raise ApprovalNotReady

    thread = str(run.conversation_id)
    saver = await conversation_checkpointer(thread, ctx, db=db, saver=deferred_saver)
    pending = await interrupt_payloads_from_checkpointer(
        {"configurable": {"thread_id": thread}}, saver
    )
    match = next((i for i in pending if i["interrupt_id"] == interrupt_id), None)
    if match is None:
        raise ApprovalNotReady
    if service.approval_actions(match["payload"]) is None:
        return None
    # A restarted worker has lost its ephemeral redaction context. The canonical
    # recorder can keep the argument digest and conceal the unverified action body.
    rows = await service.record_pending(
        db,
        run,
        [service.PendingInterrupt(interrupt_id, match["payload"], match["namespace"])],
        origin=service.RunOrigin(agent_name=name, source="chat"),
        secret_values=None,
    )
    if not rows:
        # A normal worker won the insert after this worker's lookup.
        raise ApprovalNotReady
    [row] = rows
    row.expires_at = min(service.aware(row.expires_at), expires_at)
    return row


async def publish_goal_approval(
    db: AsyncSession, ref: ChatNotificationOutbox
) -> Notification | None:
    ctx = OrgContext(org_id=ref.org_id, tenant_id=ref.tenant_id, user_id=ref.user_id)
    if not await lock_active_notice_recipient(db, ctx):
        return None
    # All supported chat resumes lock their conversation before creating a new
    # run. Serialize here too so a concurrent resume cannot receive a stale bell.
    name = await db.scalar(
        select(Conversation.agent_name_snapshot)
        .where(
            Conversation.id == ref.conversation_id,
            private_scope_predicate(Conversation, ctx),
            Conversation.source != "independent_job",
            Conversation.agent_lifecycle_state == "active",
        )
        .with_for_update()
    )
    if name is None:
        return None
    try:
        run = await get_latest_interrupted_run(db, conversation_id=ref.conversation_id, context=ctx)
    except AppError as exc:
        if exc.status in {403, 404, 409}:
            return None
        raise
    if (
        run is None
        or run.id != ref.run_id
        or not run.interrupt_id
        or run.cancel_requested_at is not None
    ):
        return None
    payload = await db.scalar(
        select(GoalTransitionIntent.payload)
        .join(ConversationGoal, ConversationGoal.id == GoalTransitionIntent.goal_id)
        .where(
            GoalTransitionIntent.id == ref.source_id,
            GoalTransitionIntent.goal_id == ref.goal_id,
            GoalTransitionIntent.goal_version == ref.goal_version,
            GoalTransitionIntent.goal_revision == ref.goal_revision,
            GoalTransitionIntent.run_id == ref.run_id,
            GoalTransitionIntent.kind == "approval_requested",
            ConversationGoal.conversation_id == ref.conversation_id,
            private_scope_predicate(ConversationGoal, ctx),
            ConversationGoal.version == ref.goal_version,
            ConversationGoal.revision == ref.goal_revision,
            ConversationGoal.status == "blocked",
            ConversationGoal.blocked_reason == "approval_pending",
        )
        .with_for_update(of=ConversationGoal)
    )
    if payload is None or payload.get("interrupt_id") != run.interrupt_id:
        return None

    # Do not filter by status or decider on the lookup: a decided or reassigned
    # request must never enter the missing-request recovery path.
    request = await db.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.run_id == run.id, ApprovalRequest.interrupt_id == run.interrupt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None:
        request = await _recover_request(db, run, ctx, name, run.interrupt_id)
    if (
        request is None
        or request.kind != "tool"
        or request.source != "chat"
        or request.status != "pending"
        or service.aware(request.expires_at) <= service.now()
        or request.tenant_id != ref.tenant_id
        or request.org_id != ref.org_id
        or request.conversation_id != ref.conversation_id
        or request.subject_user_id != ref.user_id
        or request.decider_user_id != ref.user_id
    ):
        return None

    delivered = await db.scalar(
        select(ChatNotificationOutbox.id).where(
            ChatNotificationOutbox.approval_request_id == request.id
        )
    )
    if delivered is not None:
        # This survives inbox deletion, expiry of the merge window and goal edits.
        return None
    lookup = (
        select(Notification)
        .where(
            Notification.kind == "approval.requested",
            Notification.target_type == "approval_request",
            Notification.target_id == request.id,
            Notification.user_id == ref.user_id,
            Notification.org_id == ref.org_id,
            Notification.tenant_id == ref.tenant_id,
        )
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(1)
    )
    notice = await db.scalar(lookup)
    if notice is None:
        await recording.notify_requested(db, [request])
        notice = (await db.scalars(lookup)).one()
    # A goal may disappear while its approved request remains in inbox history.
    # The bell uses this tag to re-authorize its target and never caches a name.
    notice.payload = {
        "chat_goal": True,
        "source": request.source,
        "action_count": len(request.actions),
    }
    ref.approval_request_id = request.id
    return notice
