"""A terminal decision and its independent-work notification survive or roll back together."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.config import settings
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import OrganizationMember
from app.services import independent_job_notifications as delivery
from tests.authz.test_independent_job_authority import delegation as delegation

pytestmark = pytest.mark.asyncio

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


async def _outbox(db: AsyncSession) -> list[ChatNotificationOutbox]:
    return list(await db.scalars(select(ChatNotificationOutbox)))


@pytest.mark.parametrize("source", ["initial", "resume", "chat"])
async def test_only_completed_child_or_its_resume_enqueues_once(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation, source: str
) -> None:
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    context, parent, child, job = delegation
    parent.status, parent.is_active = "completed", False
    child.status, child.is_active = ("completed" if source == "initial" else "interrupted"), False
    await db.flush()
    result = child
    if source != "initial":
        result = ConversationRun(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            conversation_id=job.child_conversation_id,
            agent_id=job.child_agent_id,
            source=source,
            parent_run_id=child.id if source == "resume" else None,
            status="completed",
            is_active=False,
            run_source="chat",
        )
        db.add(result)
        await db.flush()

    await delivery.project_job_terminal(db, parent)
    await delivery.project_job_terminal(db, result)
    await db.commit()
    await delivery.project_job_terminal(db, result)
    rows = await _outbox(db)
    if source == "chat":
        assert rows == []
    else:
        assert [
            (row.kind, row.source_id, row.run_id, row.user_id, row.conversation_id) for row in rows
        ] == [("chat_task.completed", job.id, result.id, context.user_id, child.conversation_id)]


@pytest.mark.parametrize("terminal", ["completed", "interrupted"])
async def test_rollback_restores_previous_run_without_any_notice_or_marker(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation, terminal: str
) -> None:
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    _, _, child, job = delegation
    job_id, child_id = job.id, child.id
    child.status, child.is_active = terminal, False
    if terminal == "interrupted":
        child.interrupt_id = "approval"
    await db.flush()
    await delivery.project_job_terminal(db, child)
    if terminal == "completed":
        assert len(await _outbox(db)) == 1
    else:
        await db.flush()
        await db.refresh(job)
        assert job.pending_approval_run_id == child.id

    await db.rollback()
    persisted_job = await db.get(IndependentChatJob, job_id)
    persisted_run = await db.get(ConversationRun, child_id)
    assert persisted_job is not None and persisted_job.pending_approval_run_id is None
    assert persisted_run is not None and persisted_run.status == "queued"
    assert await _outbox(db) == []


@pytest.mark.parametrize("change", ["cancel", "membership"])
async def test_terminal_completion_suppressed_for_canceled_job_or_revoked_recipient(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    delegation: Delegation,
    change: str,
) -> None:
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    context, _, child, job = delegation
    if change == "cancel":
        job.cancel_requested_at = datetime.now(UTC)
    else:
        member = await db.scalar(
            select(OrganizationMember).where(
                OrganizationMember.org_id == context.org_id,
                OrganizationMember.user_id == context.user_id,
            )
        )
        assert member is not None
        member.status = "suspended"
    child.status, child.is_active = "completed", False
    await db.flush()
    await delivery.project_job_terminal(db, child)
    assert await _outbox(db) == []


async def test_interrupted_work_retries_missing_checkpoint_then_creates_one_pending_approval(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation
) -> None:
    from unittest.mock import AsyncMock

    from app.approvals import recording
    from app.approvals.service import PendingInterrupt
    from app.models.approval_request import ApprovalRequest
    from app.models.notification import Notification

    context, _, child, job = delegation
    child.status, child.is_active, child.interrupt_id = "interrupted", False, "child-approval"
    await db.flush()
    await delivery.project_job_terminal(db, child)
    await db.commit()
    await db.refresh(job)
    assert job.pending_approval_run_id == child.id

    interruption = PendingInterrupt(
        "child-approval",
        {"action_requests": [{"name": "read_report", "args": {"report": "quarterly"}}]},
    )
    checkpoint = AsyncMock(side_effect=[[], [interruption]])
    monkeypatch.setattr(recording, "checkpoint_interrupts", checkpoint)
    await delivery.reconcile_job_approval(db, job.id, context)
    await db.commit()
    await db.refresh(job)
    assert job.pending_approval_run_id == child.id
    assert await db.scalar(select(ApprovalRequest.id)) is None

    await delivery.reconcile_job_approval(db, job.id, context)
    await db.commit()
    await delivery.reconcile_job_approval(db, job.id, context)
    await db.commit()
    await db.refresh(job)
    assert job.pending_approval_run_id is None
    approvals = list(await db.scalars(select(ApprovalRequest)))
    assert [(item.status, item.run_id, item.interrupt_id) for item in approvals] == [
        ("pending", child.id, "child-approval")
    ]
    assert list(await db.execute(select(Notification.kind, Notification.target_id))) == [
        ("approval.requested", approvals[0].id)
    ]
    assert checkpoint.await_count == 2


@pytest.mark.parametrize("superseded_by", ["cancel", "revoked", "resume"])
async def test_recovery_discards_obsolete_approval_without_reading_the_checkpoint(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation, superseded_by: str
) -> None:
    from unittest.mock import AsyncMock

    from app.approvals import recording
    from app.models.approval_request import ApprovalRequest

    context, _, child, job = delegation
    child.status, child.is_active, child.interrupt_id = "interrupted", False, "superseded"
    await db.flush()
    await delivery.project_job_terminal(db, child)
    await db.commit()
    if superseded_by == "cancel":
        job.cancel_requested_at = datetime.now(UTC)
    elif superseded_by == "revoked":
        member = await db.scalar(
            select(OrganizationMember).where(
                OrganizationMember.org_id == context.org_id,
                OrganizationMember.user_id == context.user_id,
            )
        )
        assert member is not None
        member.status = "suspended"
    else:
        db.add(
            ConversationRun(
                conversation_id=job.child_conversation_id,
                agent_id=job.child_agent_id,
                parent_run_id=child.id,
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                source="resume",
                status="running",
                is_active=True,
                run_source="chat",
            )
        )
    await db.commit()

    checkpoint = AsyncMock(return_value=[])
    monkeypatch.setattr(recording, "checkpoint_interrupts", checkpoint)
    await delivery.reconcile_job_approval(db, job.id, context)
    await db.commit()
    await db.refresh(job)
    assert job.pending_approval_run_id is None
    checkpoint.assert_not_awaited()
    assert await db.scalar(select(ApprovalRequest.id)) is None


@pytest.mark.parametrize(
    "disable_before_finish", [False, True], ids=["enabled", "disabled-after-admission"]
)
async def test_worker_terminal_transition_commits_job_completion_together_with_run(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    delegation: Delegation,
    disable_before_finish: bool,
) -> None:
    from app.agent_runtime.run_metrics import RunMetricsAccumulator
    from app.services import conversation_run_transitions, conversation_run_worker
    from tests.sqlite_test_engine import TestSession

    monkeypatch.setattr(conversation_run_worker, "async_session", TestSession)
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    context, _, child, job = delegation
    child.status, child.is_active = "running", True
    child.worker_instance_id = "finishing-worker"
    await db.commit()
    ident = child.id
    if disable_before_finish:
        monkeypatch.setattr(settings, "chat_work_notifications_enabled", False)
    result, status = await conversation_run_transitions._transition(
        ident,
        "completed",
        cancellation_ack_worker_id="finishing-worker",
        run_metrics=RunMetricsAccumulator(started_at=None),
        model_name="test-model",
        tenant=context,
    )
    assert result is not None and status == "completed"
    await db.refresh(child)
    assert child.status == "completed"
    assert [(row.source_id, row.run_id) for row in await _outbox(db)] == [(job.id, ident)]

    from app.services.chat_notification_access import open_chat_notification
    from app.services.chat_notification_outbox import drain_chat_notification_outbox

    assert await drain_chat_notification_outbox(db, tenant_id=context.tenant_id) == 1
    await db.commit()
    assert await drain_chat_notification_outbox(db, tenant_id=context.tenant_id) == 0
    [receipt] = await _outbox(db)
    assert receipt.status == "delivered" and receipt.notification_id is not None
    destination = await open_chat_notification(db, context, receipt.notification_id)
    assert (destination.job_id, destination.conversation_id, destination.run_id) == (
        job.id,
        child.conversation_id,
        ident,
    )


async def test_restarted_scheduler_continues_past_an_unavailable_child_checkpoint(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, delegation: Delegation
) -> None:
    from uuid import UUID, uuid4

    from app import database
    from app.approvals import recording
    from app.approvals.service import PendingInterrupt
    from app.models.approval_request import ApprovalRequest
    from app.models.conversation import Conversation
    from app.models.notification import Notification
    from app.services.agent_execution_acceptance import (
        OwnedExecutionRequest,
        accept_owned_execution,
    )
    from tests.sqlite_test_engine import TestSession

    context, _, first_run, first_job = delegation
    second_conversation = Conversation(
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        agent_id=first_job.child_agent_id,
        source="independent_job",
        agent_name_snapshot="Child",
        last_activity_at=datetime.now(UTC),
    )
    db.add(second_conversation)
    await db.flush()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            second_conversation.id,
            first_job.child_agent_id,
            context,
            "chat",
            None,
            run_source="start",
        ),
    )
    second_run = await db.get(ConversationRun, accepted.run_id)
    assert second_run is not None
    second_job = IndependentChatJob(
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        parent_agent_id=first_job.parent_agent_id,
        parent_run_id=first_job.parent_run_id,
        parent_conversation_id=first_job.parent_conversation_id,
        child_agent_id=first_job.child_agent_id,
        child_conversation_id=second_conversation.id,
        child_run_id=second_run.id,
        request_id=uuid4(),
        request_digest="1" * 64,
        capability_sha256=first_job.capability_sha256,
    )
    db.add(second_job)
    await db.commit()
    ordered = sorted(
        [(first_job, first_run), (second_job, second_run)], key=lambda pair: pair[0].id
    )
    for _, run in ordered:
        run.status, run.is_active, run.interrupt_id = "interrupted", False, str(run.id)
        await db.flush()
        await delivery.project_job_terminal(db, run)
    await db.commit()

    calls: list[UUID] = []
    pending = {
        job.child_conversation_id: PendingInterrupt(
            str(run.id), {"action_requests": [{"name": "read_report", "args": {}}]}
        )
        for job, run in ordered
    }
    unavailable = {ordered[0][0].child_conversation_id}

    async def read_checkpoint(conversation_id: UUID) -> list[PendingInterrupt]:
        calls.append(conversation_id)
        return [] if conversation_id in unavailable else [pending[conversation_id]]

    monkeypatch.setattr(recording, "checkpoint_interrupts", read_checkpoint)
    monkeypatch.setattr(
        delivery,
        "platform_session",
        lambda reason: database.platform_session(reason, maker=TestSession),
    )
    monkeypatch.setattr(
        delivery, "tenant_session", lambda ctx: database.tenant_session(ctx, maker=TestSession)
    )
    # The sweep opens fresh sessions. No producing worker or in-process state survives.
    await delivery.recover_independent_job_approvals()
    blocked, ready = (item[0].child_conversation_id for item in ordered)
    assert calls == [blocked, ready]
    assert list(await db.scalars(select(ApprovalRequest.conversation_id))) == [ready]
    await db.commit()

    unavailable.clear()
    await delivery.recover_independent_job_approvals()
    await delivery.recover_independent_job_approvals()
    assert calls == [blocked, ready, blocked]
    approvals = list(await db.scalars(select(ApprovalRequest)))
    assert {(item.conversation_id, item.status) for item in approvals} == {
        (blocked, "pending"),
        (ready, "pending"),
    }
    assert set(
        await db.scalars(
            select(Notification.target_id).where(Notification.kind == "approval.requested")
        )
    ) == {item.id for item in approvals}
