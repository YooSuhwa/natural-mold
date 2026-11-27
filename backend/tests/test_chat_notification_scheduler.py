"""Leadership, tenant fairness, and transaction isolation of notification delivery."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import scheduler_chat_notifications as job
from app import scheduler_runtime
from app.config import settings
from app.database import SCOPE_INFO_KEY
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.scheduler_job_ids import LeaderSchedulerJobId
from tests.sqlite_test_engine import TestSession


def _notice(
    tenant_id: UUID, *, due: bool = True, delivered: bool = False
) -> ChatNotificationOutbox:
    now = datetime.now(UTC)
    return ChatNotificationOutbox(
        id=uuid4(),
        tenant_id=tenant_id,
        org_id=uuid4(),
        user_id=uuid4(),
        conversation_id=uuid4(),
        run_id=uuid4(),
        kind="chat_task.completed",
        source_id=uuid4(),
        status="delivered" if delivered else "pending",
        finished_at=now if delivered else None,
        available_at=now + timedelta(hours=1) if not due else now - timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_scheduler_rotates_only_due_companies(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [UUID(int=n) for n in range(1, 6)]
    db.add_all(
        [
            _notice(ids[0]),
            _notice(ids[0]),
            _notice(ids[1], due=False),
            _notice(ids[2], delivered=True),
            _notice(ids[3]),
            _notice(ids[4]),
        ]
    )
    await db.commit()
    monkeypatch.setattr(job, "async_session", TestSession)
    monkeypatch.setattr(job, "_TENANTS_PER_TICK", 2)
    assert await job._due_tenant_ids(None) == [ids[0], ids[3]]
    assert await job._due_tenant_ids(ids[3]) == [ids[4], ids[0]]


@pytest.mark.asyncio
async def test_pending_work_drains_after_disable_and_tenant_failure_isolated(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import chat_notification_outbox

    tenants = [UUID(int=1), UUID(int=2), UUID(int=3)]
    db.add_all(_notice(tenant_id) for tenant_id in tenants)
    await db.commit()
    processed: list[UUID] = []

    async def deliver(session: AsyncSession, *, tenant_id: UUID, limit: int) -> int:
        assert session.info[SCOPE_INFO_KEY] == {"app.tenant_id": str(tenant_id)}
        assert limit == 50
        processed.append(tenant_id)
        row = await session.scalar(
            select(ChatNotificationOutbox).where(ChatNotificationOutbox.tenant_id == tenant_id)
        )
        assert row is not None
        row.attempt_count += 1
        await session.flush()
        if tenant_id == tenants[0]:
            raise RuntimeError("transient tenant failure")
        return 1

    healthy = AsyncMock(side_effect=[True, True, False])
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", False)
    monkeypatch.setattr(job, "async_session", TestSession)
    monkeypatch.setattr(job, "_last_tenant", None)
    monkeypatch.setattr(job, "scheduler_leader_is_healthy", healthy)
    monkeypatch.setattr(chat_notification_outbox, "drain_chat_notification_outbox", deliver)
    await job.drain_due_chat_notifications()

    assert processed == tenants[:2]
    assert list(
        await db.scalars(
            select(ChatNotificationOutbox.attempt_count).order_by(ChatNotificationOutbox.tenant_id)
        )
    ) == [0, 1, 0]


@pytest.mark.asyncio
async def test_registration_reuses_fenced_job_even_without_new_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    scheduler.start(paused=True)
    try:
        monkeypatch.setattr(settings, "chat_work_notifications_enabled", False)
        monkeypatch.setattr(job, "get_scheduler", lambda: scheduler)
        job.register_chat_notification_job()
        job.register_chat_notification_job()
        registered = scheduler.get_jobs()
        assert len(registered) == 1
        assert registered[0].func is scheduler_runtime.run_leader_scheduler_job
        assert registered[0].args == (LeaderSchedulerJobId.CHAT_NOTIFICATION_DRAIN,)
        assert registered[0].coalesce and registered[0].max_instances == 1

        drain = AsyncMock()
        monkeypatch.setattr(scheduler_runtime, "drain_due_chat_notifications", drain)
        monkeypatch.setattr(
            scheduler_runtime, "scheduler_leader_is_healthy", AsyncMock(side_effect=[False, True])
        )
        await registered[0].func(*registered[0].args)
        drain.assert_not_awaited()
        await registered[0].func(*registered[0].args)
        drain.assert_awaited_once()
    finally:
        scheduler.shutdown(wait=False)
