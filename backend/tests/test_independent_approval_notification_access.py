"""A delivered approval bell follows the delegated work's authority across its lifetime."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.service import Claim, PendingInterrupt, RunOrigin, claim, record_pending
from app.authz.notification_queries import list_notifications
from app.authz.notifications import Target, notify
from app.authz.org_context import OrgContext
from app.authz.runtime import runtime_authz
from app.models.agent_subagent import AgentSubAgentLink
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import NotificationView
from tests.authz.test_independent_job_authority import delegation as delegation

pytestmark = pytest.mark.asyncio

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


async def _approval(db: AsyncSession, run: ConversationRun, source: str) -> ApprovalRequest:
    run.status, run.is_active, run.interrupt_id = "interrupted", False, "review-report"
    await db.flush()
    interrupt = PendingInterrupt(
        run.interrupt_id,
        {
            "action_requests": [{"name": "review_report", "args": {}}],
            "review_configs": [{"action_name": "review_report", "allowed_decisions": ["approve"]}],
        },
    )
    (row,) = await record_pending(
        db, run, [interrupt], origin=RunOrigin(source=source, agent_name="Confidential reviewer")
    )
    return row


async def _publish(
    db: AsyncSession, context: OrgContext, kind: str, target: Target, actor_id: UUID
) -> list[Notification]:
    return await notify(
        db,
        kind=kind,
        recipients=[context.user_id],
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        target=target,
        actor_id=actor_id,
        payload={"source": "subagent", "action_count": 1},
        link="/approvals",
    )


async def _bell(db: AsyncSession, context: OrgContext, notice_id: UUID) -> NotificationView:
    page = await list_notifications(
        db, runtime_authz(), context, cursor=None, limit=20, unread=False
    )
    return next(item for item in page.items if item.id == notice_id)


@pytest.mark.parametrize("kind", ["approval.requested", "approval.expiring", "approval.decided"])
@pytest.mark.parametrize("invalidation", ["delete_parent", "delete_child", "revoke_delegation"])
async def test_approval_bell_rechecks_the_independent_job(
    db: AsyncSession, delegation: Delegation, kind: str, invalidation: str
) -> None:
    context, _, child, job = delegation
    row = await _approval(db, child, "subagent")
    target = Target("approval_request", row.id, row.agent_name)
    actor = User(email="approval-author@example.test", name="Confidential author")
    db.add(actor)
    await db.flush()
    if kind == "approval.decided":
        resumed = ConversationRun(
            conversation_id=child.conversation_id,
            agent_id=child.agent_id,
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            org_id=context.org_id,
            parent_run_id=child.id,
            source="resume",
            status="completed",
            is_active=False,
        )
        db.add(resumed)
        await db.flush()
        (decided,) = await claim(
            db,
            Claim(
                child.id,
                {row.interrupt_id: ["approve"]},
                "inbox",
                context.user_id,
                resumed_run_id=resumed.id,
            ),
        )
        assert decided.status == "approved" and decided.resumed_run_id == resumed.id
    (stored,) = await _publish(db, context, kind, target, actor.id)
    # Another delivery after the loss of authority would create a new row, even with deduplication.
    stored.created_at = datetime.now(UTC) - timedelta(days=2)
    notice_id, actor_id = stored.id, actor.id
    await db.commit()

    assert stored.payload["independent_job"] is True
    visible = await _bell(db, context, notice_id)
    assert visible.target and visible.target.accessible
    assert (visible.target.id, visible.target.name) == (target.id, target.name)
    assert visible.actor and (visible.actor.id, visible.actor.display_name) == (
        actor_id,
        actor.name,
    )
    assert visible.link == "/approvals"
    assert visible.payload == {"source": "subagent", "action_count": 1}

    if invalidation == "revoke_delegation":
        edge = await db.get(AgentSubAgentLink, (job.parent_agent_id, job.child_agent_id))
        assert edge
        await db.delete(edge)
    else:
        conversation_id = (
            job.parent_conversation_id
            if invalidation == "delete_parent"
            else job.child_conversation_id
        )
        await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await db.commit()

    hidden = await _bell(db, context, notice_id)
    assert hidden.target and not hidden.target.accessible
    assert hidden.target.id is None and hidden.target.name is None
    assert hidden.actor is None and hidden.link is None and hidden.payload == {}
    assert await _publish(db, context, kind, target, actor_id) == []
    assert (
        await db.scalar(select(func.count(Notification.id)).where(Notification.kind == kind)) == 1
    )


@pytest.mark.parametrize("source", ["chat", "subagent"])
async def test_ordinary_approval_retains_its_target_even_with_a_delegated_source(
    db: AsyncSession, delegation: Delegation, source: str
) -> None:
    context, parent, _, _ = delegation
    row = await _approval(db, parent, source)
    (stored,) = await notify(
        db,
        kind="approval.requested",
        recipients=[context.user_id],
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        target=Target("approval_request", row.id, row.agent_name),
        payload={"source": row.source, "independent_job": True},
        link="/approvals",
    )
    await db.commit()

    assert "independent_job" not in stored.payload
    visible = await _bell(db, context, stored.id)
    assert visible.target and visible.target.accessible and visible.target.id == row.id
    assert visible.target.name == row.agent_name and visible.link == "/approvals"
    assert visible.payload == {"source": source}
