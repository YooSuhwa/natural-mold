"""PostgreSQL transaction and RLS contracts for durable chat notifications."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.approvals import recording, service
from app.authz.conversation_agent_checks import current_authz
from app.authz.conversation_scope import lock_private_conversation
from app.authz.notification_queries import list_notifications
from app.authz.notifications import _lock_key
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import SessionTenant, tenant_session
from app.models.approval_request import ApprovalRequest
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_goal import ConversationGoal, GoalAttempt
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.notification import Notification
from app.models.organization import OrganizationMember
from app.schemas.conversation_goal import GoalActionRequest
from app.schemas.independent_chat_job import IndependentJobCreate
from app.services import chat_notification_outbox as outbox
from app.services.chat_notification_access import open_chat_notification
from app.services.conversation_goal_service import apply_goal_action, block_goal
from app.services.conversation_run_state import transition_run
from app.services.independent_jobs import accept_independent_job
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.integration.test_independent_chat_job_idempotency import _accepted_parent

pytestmark = pytest.mark.integration
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as database:
        yield database


@pytest.fixture
async def sessions(lab: RlsLab, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Sessions]:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    engine = create_async_engine(async_url(lab.app_url))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def completed_job(
    lab: RlsLab, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[OrgContext, UUID, UUID, UUID]]:
    monkeypatch.setattr(settings, "independent_chat_jobs_enabled", True)
    engine = create_async_engine(async_url(lab.admin_url))
    admin = async_sessionmaker(engine, expire_on_commit=False)
    try:
        ctx, parent_id, parent_run_id, target_id = await _accepted_parent(admin)
        async with admin() as db:
            parent = await db.get(Conversation, parent_id)
            assert parent is not None
            accepted = await accept_independent_job(
                db,
                parent,
                parent_run_id,
                IndependentJobCreate(
                    request_id=uuid4(), target_id=target_id, instruction="Read the report."
                ),
                ctx,
            )
            job = accepted.job
            run = await db.get(ConversationRun, job.child_run_id)
            assert run is not None
            await transition_run(db, run, "running", worker_instance_id="fixture-worker")
            await transition_run(db, run, "completed")
            await db.commit()
            try:
                yield ctx, job.child_conversation_id, run.id, job.id
            finally:
                await db.execute(
                    delete(Conversation).where(
                        Conversation.id.in_([parent_id, job.child_conversation_id])
                    )
                )
                await db.commit()
    finally:
        await engine.dispose()


async def test_contending_completion_producers_store_one_company_scoped_intent(
    sessions: Sessions,
    completed_job: tuple[OrgContext, UUID, UUID, UUID],
    lab: RlsLab,
) -> None:
    ctx, conversation_id, run_id, job_id = completed_job

    async def enqueue(db: AsyncSession) -> None:
        await outbox.enqueue_independent_completion(
            db,
            job_id=job_id,
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )

    async with (
        tenant_session(ctx, maker=sessions) as first,
        tenant_session(ctx, maker=sessions) as second,
    ):
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        await enqueue(first)
        async with asyncio.timeout(10), asyncio.TaskGroup() as racers:
            racers.create_task(enqueue(second))
            # Require database-observed lock contention rather than scheduler timing.
            while not await first.scalar(  # noqa: ASYNC110 - observed PostgreSQL lock
                text("SELECT :first = ANY(pg_blocking_pids(:second))"),
                {"first": first_pid, "second": second_pid},
            ):
                await asyncio.sleep(0.01)
            await first.commit()
        await second.commit()

    count = (
        select(func.count())
        .select_from(ChatNotificationOutbox)
        .where(ChatNotificationOutbox.source_id == job_id)
    )
    async with tenant_session(ctx, maker=sessions) as own:
        assert await own.scalar(count) == 1
    async with tenant_session(SessionTenant(lab.tenant_b), maker=sessions) as foreign:
        assert await foreign.scalar(count) == 0


async def test_skipped_locked_delivery_survives_worker_rollback_and_reconnect(
    sessions: Sessions,
    completed_job: tuple[OrgContext, UUID, UUID, UUID],
) -> None:
    ctx, conversation_id, run_id, job_id = completed_job
    worker = SessionTenant(ctx.tenant_id)
    async with tenant_session(ctx, maker=sessions) as producer:
        await outbox.enqueue_independent_completion(
            producer,
            job_id=job_id,
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        await producer.commit()

    async def drain(db: AsyncSession) -> int:
        return await outbox.drain_chat_notification_outbox(db, tenant_id=ctx.tenant_id)

    notices = select(Notification).where(Notification.user_id == ctx.user_id)
    async with tenant_session(worker, maker=sessions) as interrupted:
        assert await drain(interrupted) == 1
        async with tenant_session(worker, maker=sessions) as competitor:
            async with asyncio.timeout(5):
                assert await drain(competitor) == 0
            assert await competitor.scalar(notices) is None
        await interrupted.rollback()

    async with tenant_session(worker, maker=sessions) as restarted:
        assert await drain(restarted) == 1
        await restarted.commit()

    async with tenant_session(ctx, maker=sessions) as reader:
        [notice] = (await reader.scalars(notices)).all()
        read_at = datetime.now(UTC)
        notice.read_at = read_at
        await reader.commit()
        assert await drain(reader) == 0
        [again] = (await reader.scalars(notices)).all()
        receipt = await reader.scalar(
            select(ChatNotificationOutbox).where(
                ChatNotificationOutbox.source_id == job_id,
            )
        )
        assert again.id == notice.id and again.read_at == read_at
        assert receipt is not None and receipt.notification_id == notice.id


async def test_disabling_new_notices_honors_an_independent_jobs_prior_admission_once(
    sessions: Sessions,
    completed_job: tuple[OrgContext, UUID, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, conversation_id, run_id, admitted_job = completed_job
    later_job = uuid4()
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", False)

    async with tenant_session(ctx, maker=sessions) as producer:
        complete = partial(
            outbox.enqueue_independent_completion,
            producer,
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        await complete(job_id=admitted_job, admitted_with_notice=True)
        await complete(job_id=admitted_job, admitted_with_notice=True)
        await complete(job_id=later_job)
        await producer.commit()

    worker_scope = SessionTenant(ctx.tenant_id)
    async with tenant_session(worker_scope, maker=sessions) as worker:
        assert await outbox.drain_chat_notification_outbox(worker, tenant_id=ctx.tenant_id) == 1
        await worker.commit()
        assert await outbox.drain_chat_notification_outbox(worker, tenant_id=ctx.tenant_id) == 0

    async with tenant_session(ctx, maker=sessions) as reader:
        [notice] = (
            await reader.scalars(select(Notification).where(Notification.user_id == ctx.user_id))
        ).all()
        [receipt] = (
            await reader.scalars(
                select(ChatNotificationOutbox).where(ChatNotificationOutbox.user_id == ctx.user_id)
            )
        ).all()
        assert (receipt.source_id, receipt.run_id, receipt.notification_id) == (
            admitted_job,
            run_id,
            notice.id,
        )


@pytest.fixture
async def completed_run(lab: RlsLab) -> AsyncIterator[tuple[OrgContext, UUID, UUID]]:
    engine = create_async_engine(async_url(lab.admin_url))
    admin = async_sessionmaker(engine, expire_on_commit=False)
    try:
        ctx, conversation_id, run_id, _ = await _accepted_parent(admin)
        async with admin() as db:
            try:
                yield ctx, conversation_id, run_id
            finally:
                await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
                await db.commit()
    finally:
        await engine.dispose()


async def test_goal_approval_worker_reuses_the_read_approval_across_rollback(
    sessions: Sessions,
    completed_run: tuple[OrgContext, UUID, UUID],
) -> None:
    ctx, conversation_id, run_id = completed_run
    interrupt_id = "confirm-export"
    read_at = datetime.now(UTC) - timedelta(minutes=15)
    async with tenant_session(ctx, maker=sessions) as producer:
        conversation = await producer.get(Conversation, conversation_id)
        run = await producer.get(ConversationRun, run_id)
        assert conversation is not None and run is not None
        conversation.source = "ui"
        run.status = "interrupted"
        run.run_source = "CHAT"
        run.interrupt_id = interrupt_id
        goal = ConversationGoal(
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            conversation_id=conversation_id,
            title="Approve an export",
            criteria=[],
        )
        producer.add(goal)
        await producer.flush()
        [request] = await service.record_pending(
            producer,
            run,
            [
                service.PendingInterrupt(
                    interrupt_id,
                    {
                        "action_requests": [{"name": "export", "args": {}}],
                        "review_configs": [
                            {"action_name": "export", "allowed_decisions": ["approve", "reject"]}
                        ],
                    },
                )
            ],
            origin=service.RunOrigin("Task"),
            secret_values=[],
        )
        await recording.notify_requested(producer, [request])
        canonical = (
            await producer.scalars(
                select(Notification).where(
                    Notification.user_id == ctx.user_id,
                    Notification.target_id == request.id,
                )
            )
        ).one()
        # This notice is already read and beyond notify()'s one-hour merge window.
        canonical.created_at = read_at - timedelta(hours=2)
        canonical.read_at = read_at
        attempt = GoalAttempt(
            goal_id=goal.id, goal_version=goal.version, run_id=run.id, steer_seq=0
        )
        producer.add(attempt)
        await block_goal(
            producer, goal, "approval_pending", attempt=attempt, interrupt_id=interrupt_id
        )
        await producer.commit()
        canonical_id, request_id = canonical.id, request.id

    scope = SessionTenant(ctx.tenant_id)
    async with tenant_session(scope, maker=sessions) as first:
        assert await outbox.drain_chat_notification_outbox(first, tenant_id=ctx.tenant_id) == 1
        async with tenant_session(scope, maker=sessions) as second:
            async with asyncio.timeout(5):
                assert (
                    await outbox.drain_chat_notification_outbox(second, tenant_id=ctx.tenant_id)
                    == 0
                )
        await first.rollback()
    async with tenant_session(scope, maker=sessions) as recovered:
        assert await outbox.drain_chat_notification_outbox(recovered, tenant_id=ctx.tenant_id) == 1
        await recovered.commit()
    async with tenant_session(ctx, maker=sessions) as reader:
        [notice] = (
            await reader.scalars(select(Notification).where(Notification.user_id == ctx.user_id))
        ).all()
        [receipt] = (
            await reader.scalars(
                select(ChatNotificationOutbox).where(ChatNotificationOutbox.user_id == ctx.user_id)
            )
        ).all()
        assert (notice.id, notice.target_id, notice.kind, notice.read_at) == (
            canonical_id,
            request_id,
            "approval.requested",
            read_at,
        )
        assert notice.payload.get("chat_goal") is True
        assert (receipt.status, receipt.approval_request_id, receipt.notification_id) == (
            "delivered",
            request_id,
            canonical_id,
        )
        assert list(
            await reader.scalars(select(ApprovalRequest.id).where(ApprovalRequest.run_id == run_id))
        ) == [request_id]


@pytest.fixture
async def blocked_goal(
    sessions: Sessions,
    completed_run: tuple[OrgContext, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[OrgContext, ChatNotificationOutbox]:
    ctx, conversation_id, run_id = completed_run
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({ctx.org_id}))
    async with tenant_session(ctx, maker=sessions) as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.source = "ui"
        goal = ConversationGoal(
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            conversation_id=conversation_id,
            title="Review the export",
            criteria=[],
        )
        db.add(goal)
        await db.flush()
        attempt = GoalAttempt(
            goal_id=goal.id, goal_version=goal.version, run_id=run_id, steer_seq=0
        )
        db.add(attempt)
        await block_goal(db, goal, "manual_review", attempt=attempt)
        await db.commit()
        notice = (
            await db.scalars(
                select(ChatNotificationOutbox).where(ChatNotificationOutbox.goal_id == goal.id)
            )
        ).one()
        return ctx, notice


async def _cancel_goal(db: AsyncSession, ctx: OrgContext, receipt: ChatNotificationOutbox) -> None:
    conversation = await lock_private_conversation(db, receipt.conversation_id, ctx)
    assert receipt.goal_revision is not None
    await apply_goal_action(
        db,
        conversation,
        GoalActionRequest(action="cancel", expected_revision=receipt.goal_revision),
    )


async def _wait_for_blocker(db: AsyncSession, *, blocker: int, waiter: int) -> None:
    async with asyncio.timeout(8):
        while not await db.scalar(  # noqa: ASYNC110 - observe the actual lock order in PostgreSQL
            text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"),
            {"blocker": blocker, "waiter": waiter},
        ):
            await asyncio.sleep(0.01)


async def test_goal_edit_already_holds_lock_before_notice_delivery(
    sessions: Sessions, blocked_goal: tuple[OrgContext, ChatNotificationOutbox]
) -> None:
    ctx, ref = blocked_goal
    tenant = SessionTenant(ctx.tenant_id)
    async with (
        tenant_session(ctx, maker=sessions) as editor,
        tenant_session(tenant, maker=sessions) as worker,
    ):
        await _cancel_goal(editor, ctx, ref)
        async with asyncio.timeout(5):
            assert await outbox.drain_chat_notification_outbox(worker, tenant_id=ctx.tenant_id) == 1
        await worker.commit()
        receipt = await worker.get(ChatNotificationOutbox, ref.id)
        assert receipt is not None
        assert (receipt.status, receipt.attempt_count, receipt.notification_id) == (
            "pending",
            1,
            None,
        )
        await editor.commit()

    async with tenant_session(tenant, maker=sessions) as restarted:
        assert await outbox.drain_chat_notification_outbox(restarted, tenant_id=ctx.tenant_id) == 1
        await restarted.commit()
        receipt = await restarted.get(ChatNotificationOutbox, ref.id)
        assert receipt is not None
        assert (receipt.status, receipt.attempt_count, receipt.notification_id) == (
            "suppressed",
            2,
            None,
        )
        assert not await restarted.scalar(
            select(Notification.id).where(Notification.target_id == ref.id)
        )


async def test_goal_edit_waits_until_validated_notice_is_committed(
    sessions: Sessions, blocked_goal: tuple[OrgContext, ChatNotificationOutbox]
) -> None:
    ctx, ref = blocked_goal
    async with (
        tenant_session(ctx, maker=sessions) as gate,
        tenant_session(SessionTenant(ctx.tenant_id), maker=sessions) as worker,
        tenant_session(ctx, maker=sessions) as editor,
    ):
        gate_pid = await gate.scalar(text("SELECT pg_backend_pid()"))
        worker_pid = await worker.scalar(text("SELECT pg_backend_pid()"))
        editor_pid = await editor.scalar(text("SELECT pg_backend_pid()"))
        assert gate_pid is not None and worker_pid is not None and editor_pid is not None
        # Stop the real notifier just after target validation but before its INSERT.
        await _lock_key(gate, ctx.user_id, ref.kind, ref.id, ctx.tenant_id, ctx.org_id)
        async with asyncio.TaskGroup() as racers:
            delivery = racers.create_task(
                outbox.drain_chat_notification_outbox(worker, tenant_id=ctx.tenant_id)
            )
            await _wait_for_blocker(gate, blocker=gate_pid, waiter=worker_pid)
            edit = racers.create_task(_cancel_goal(editor, ctx, ref))
            await _wait_for_blocker(gate, blocker=worker_pid, waiter=editor_pid)
            await gate.commit()
            assert await delivery == 1
            await worker.commit()
            await edit
            await editor.commit()
        [published] = (
            await gate.scalars(select(Notification).where(Notification.target_id == ref.id))
        ).all()
        assert published.kind == "chat_goal.blocked"
        assert (
            await gate.scalar(
                select(ConversationGoal.status).where(ConversationGoal.id == ref.goal_id)
            )
            == "canceled"
        )


@pytest.mark.parametrize("removed", ["parent", "membership"])
@pytest.mark.parametrize("commit_delete", [False, True], ids=["rollback", "commit"])
async def test_access_removal_overlapping_delivery_retries_before_publishing(
    sessions: Sessions,
    completed_job: tuple[OrgContext, UUID, UUID, UUID],
    commit_delete: bool,
    removed: str,
) -> None:
    ctx, conversation_id, run_id, job_id = completed_job
    receipt_for_job = select(ChatNotificationOutbox).where(
        ChatNotificationOutbox.source_id == job_id
    )
    notices = select(Notification.id).where(Notification.user_id == ctx.user_id)
    async with tenant_session(ctx, maker=sessions) as producer:
        job = await producer.get(IndependentChatJob, job_id)
        assert job is not None
        parent_id = job.parent_conversation_id
        await outbox.enqueue_independent_completion(
            producer,
            job_id=job_id,
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        await producer.commit()

    worker = SessionTenant(ctx.tenant_id)
    if removed == "parent":
        removal = (
            delete(Conversation).where(Conversation.id == parent_id).returning(Conversation.id)
        )
        expected_id = parent_id
    else:
        removal = (
            delete(OrganizationMember)
            .where(
                OrganizationMember.org_id == ctx.org_id, OrganizationMember.user_id == ctx.user_id
            )
            .returning(OrganizationMember.user_id)
        )
        expected_id = ctx.user_id
    async with tenant_session(ctx, maker=sessions) as deleting:
        assert await deleting.scalar(removal) == expected_id
        # The other connection sees the previous committed access while the
        # writer owns the parent/job or organization membership row lock.
        async with tenant_session(worker, maker=sessions) as first:
            async with asyncio.timeout(4):
                assert (
                    await outbox.drain_chat_notification_outbox(first, tenant_id=ctx.tenant_id) == 1
                )
            await first.commit()
        async with tenant_session(ctx, maker=sessions) as reader:
            receipt = (await reader.scalars(receipt_for_job)).one()
            assert (
                receipt.status,
                receipt.attempt_count,
                receipt.notification_id,
                receipt.finished_at,
            ) == ("pending", 1, None, None)
            assert await reader.scalar(notices) is None
            assert receipt.available_at >= receipt.created_at + timedelta(seconds=4)
        if commit_delete:
            await deleting.commit()
        else:
            await deleting.rollback()

    # Make the scheduled retry due using the database clock.
    async with tenant_session(worker, maker=sessions) as retry:
        await retry.execute(
            update(ChatNotificationOutbox)
            .where(ChatNotificationOutbox.source_id == job_id)
            .values(available_at=func.clock_timestamp() - timedelta(seconds=1))
        )
        assert await outbox.drain_chat_notification_outbox(retry, tenant_id=ctx.tenant_id) == 1
        await retry.commit()
    async with tenant_session(ctx, maker=sessions) as reader:
        receipt = (await reader.scalars(receipt_for_job)).one()
        assert receipt.attempt_count == 2 and receipt.finished_at is not None
        if commit_delete:
            assert receipt.status == "suppressed" and receipt.notification_id is None
            assert await reader.scalar(notices) is None
            return
        notice_id = (await reader.scalars(notices)).one()
        assert receipt.status == "delivered" and receipt.notification_id == notice_id

    # Readers use the committed snapshot without claiming publication locks.
    async with tenant_session(ctx, maker=sessions) as deleting:
        assert await deleting.scalar(removal) == expected_id
        async with tenant_session(ctx, maker=sessions) as reader, asyncio.timeout(4):
            page = await list_notifications(
                reader, current_authz(), ctx, cursor=None, limit=5, unread=False
            )
            [visible] = page.items
            assert (
                visible.id == notice_id and visible.target is not None and visible.target.accessible
            )
            dest = await open_chat_notification(reader, ctx, notice_id)
            assert (dest.job_id, dest.conversation_id, dest.run_id) == (
                job_id,
                conversation_id,
                run_id,
            )
        await deleting.rollback()
