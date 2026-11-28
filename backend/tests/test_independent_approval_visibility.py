from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import inbox, recording, service
from app.approvals.independent_visibility import authorized_independent_approval_ids
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.notification import Notification
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.test_independent_job_approvals import Delegation, approval, pause_job

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("loss", ["parent_deleted", "delegation_revoked", "child_archived"])
@pytest.mark.parametrize("due", ["notice", "expiry"])
async def test_unavailable_background_request_is_hidden_and_never_reminded_or_resumed(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation, loss: str, due: str
) -> None:
    context, parent, child, job = delegation
    await pause_job(db, child, job)
    monkeypatch.setattr(
        recording, "checkpoint_interrupts", AsyncMock(return_value=[approval("read-approval")])
    )
    [row] = await recording.record_interrupted_run(child.id, context)
    assert await authorized_independent_approval_ids(db, context, [row.id]) == {row.id}
    assert [r.id for r in await service.list_for_decider(db, context, pending=True, limit=10)] == [
        row.id
    ]
    if loss == "parent_deleted":
        await db.execute(delete(Conversation).where(Conversation.id == parent.conversation_id))
    elif loss == "child_archived":
        target = await db.get(Agent, job.child_agent_id)
        assert target is not None
        target.archived_at = service.now()
    else:
        link = await db.get(AgentSubAgentLink, (job.parent_agent_id, job.child_agent_id))
        assert link is not None
        link.link_status = "broken"
    request = await db.get(ApprovalRequest, row.id)
    assert request is not None
    request.expires_at = service.now() + timedelta(minutes=10 if due == "notice" else -1)
    await db.commit()

    assert not await authorized_independent_approval_ids(db, context, [row.id])
    assert await service.list_for_decider(db, context, pending=True, limit=10) == []
    assert await service.pending_count(db, context) == 0
    resume = AsyncMock()
    monkeypatch.setattr(inbox, "_respond", resume)
    with pytest.raises(inbox.InboxError) as denial:
        await inbox.decide(
            db,
            approval_id=row.id,
            decisions=[{"type": "approve"}],
            user=CurrentUser(id=context.user_id, email="user@example.com", name="Owner"),
            context=context,
            request=None,
        )
    assert denial.value.status == 404
    if due == "notice":
        assert await inbox.notify_expiring() == 0
    else:
        assert await inbox.expire_due() == 1
    await db.refresh(request)
    assert request.status == "canceled" and request.reason == "independent_job_unavailable"
    assert list(await db.scalars(select(Notification.kind))) == ["approval.requested"]
    resume.assert_not_awaited()


async def test_remote_revocation_filters_an_existing_approval_and_outage_retains_its_reminder(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation
) -> None:
    from unittest.mock import Mock

    from app.approvals import independent_visibility
    from app.exceptions import AppError

    context, _, child, job = delegation
    await pause_job(db, child, job)
    monkeypatch.setattr(
        recording, "checkpoint_interrupts", AsyncMock(return_value=[approval("read-approval")])
    )
    [row] = await recording.record_interrupted_run(child.id, context)
    parent = f"agent:{job.parent_agent_id}"
    target = f"agent:{job.child_agent_id}"
    policy = Mock()
    policy.enforces.return_value = True
    policy.filter_visible = AsyncMock(return_value=[parent])
    monkeypatch.setattr(independent_visibility, "current_authz", lambda: policy)

    assert not await authorized_independent_approval_ids(db, context, [row.id])
    assert await service.list_for_decider(db, context, pending=True, limit=10) == []
    assert await service.pending_count(db, context) == 0
    policy.filter_visible.return_value = [parent, target]
    assert await authorized_independent_approval_ids(db, context, [row.id]) == {row.id}

    item = await db.get(ApprovalRequest, row.id)
    assert item is not None
    item.expires_at = service.now() + timedelta(minutes=10)
    await db.commit()
    policy.filter_visible.side_effect = AppError("unavailable", "temporarily unavailable", 503)
    assert await inbox.notify_expiring() == 0
    await db.refresh(item)
    assert item.status == "pending" and item.expiring_notified_at is None
    assert await service.pending_count(db, context) == 0
    policy.filter_visible.side_effect = None
    assert await inbox.notify_expiring() == 1
