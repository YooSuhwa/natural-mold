"""The child checkpoint, approval and notification remain bound to one pending decision."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import recording, service
from app.authz.org_context import OrgContext
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.notification import Notification
from tests.authz.test_independent_job_authority import delegation as delegation

pytestmark = pytest.mark.asyncio

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]
SECRET = "value-known-only-to-the-original-runtime"


def approval(interrupt_id: str) -> service.PendingInterrupt:
    return service.PendingInterrupt(
        interrupt_id,
        {
            "action_requests": [
                {
                    "name": "read_report",
                    "args": {"dest": "Q3", "detail": SECRET},
                    "description": f"Report details {SECRET}",
                }
            ],
            "review_configs": [
                {"action_name": "read_report", "allowed_decisions": ["approve", "reject"]}
            ],
        },
    )


async def pause_job(db: AsyncSession, child: ConversationRun, job: IndependentChatJob) -> None:
    child.status, child.is_active, child.interrupt_id = "interrupted", False, "read-approval"
    job.pending_approval_run_id = child.id
    await db.commit()


async def test_live_hook_records_and_notifies_once_with_pinned_redaction(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation
) -> None:
    context, _, child, job = delegation
    await pause_job(db, child, job)
    checkpoint = AsyncMock(return_value=[approval("read-approval")])
    monkeypatch.setattr(recording, "checkpoint_interrupts", checkpoint)
    [row] = await recording.record_interrupted_run(child.id, context, secret_values=[SECRET])
    assert await recording.record_interrupted_run(child.id, context, secret_values=[SECRET]) == []
    assert row.actions[0]["args"] == {"dest": "Q3", "detail": "<redacted>"}
    assert SECRET not in str(row.actions)
    assert row.source == "subagent" and row.status == "pending"
    assert row.decider_user_id == context.user_id and row.resumed_run_id is None
    assert row.args_digest == service.args_digest(approval("read-approval").payload)
    assert list(await db.scalars(select(Notification.target_id))) == [row.id]
    await db.refresh(job)
    assert job.pending_approval_run_id is None
    checkpoint.assert_awaited_once_with(job.child_conversation_id)


async def test_crash_before_feed_commit_leaves_marker_and_private_retry(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation
) -> None:
    context, _, child, job = delegation
    await pause_job(db, child, job)
    ident, run_id = job.id, child.id
    checkpoint = AsyncMock(return_value=[approval("read-approval")])
    monkeypatch.setattr(recording, "checkpoint_interrupts", checkpoint)
    with monkeypatch.context() as fault:
        fault.setattr(recording, "notify_requested", AsyncMock(side_effect=RuntimeError("crash")))
        with pytest.raises(RuntimeError, match="crash"):
            await recording.record_interrupted_run(child.id, context, secret_values=[SECRET])
    await db.refresh(job)
    assert job.pending_approval_run_id == run_id
    assert await db.scalar(select(ApprovalRequest.id)) is None
    assert await db.scalar(select(Notification.id)) is None
    from app.services.independent_job_notifications import reconcile_job_approval

    assert await reconcile_job_approval(db, ident, context)
    await db.commit()
    [row] = list(await db.scalars(select(ApprovalRequest)))
    assert row.actions[0]["args"] == "<redacted>"
    assert row.actions[0]["description"] is None
    assert SECRET not in str(row.actions)
    assert row.args_digest == service.args_digest(approval("read-approval").payload)
    assert row.resumed_run_id is None
    assert list(await db.scalars(select(Notification.target_id))) == [row.id]


async def test_checkpoint_must_match_persisted_child_interrupt(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation
) -> None:
    context, _, child, job = delegation
    await pause_job(db, child, job)
    question = service.PendingInterrupt(
        "read-approval",
        {"action_requests": [{"name": "ask_user", "args": {"question": "Which quarter?"}}]},
    )
    checkpoint = AsyncMock(side_effect=[[approval("old-decision")], [question]])
    monkeypatch.setattr(recording, "checkpoint_interrupts", checkpoint)
    assert await recording.record_interrupted_run(child.id, context) == []
    await db.refresh(job)
    assert job.pending_approval_run_id == child.id
    assert await recording.record_interrupted_run(child.id, context) == []
    await db.refresh(job)
    assert job.pending_approval_run_id is None
    assert await db.scalar(select(ApprovalRequest.id)) is None
    assert await db.scalar(select(Notification.id)) is None
