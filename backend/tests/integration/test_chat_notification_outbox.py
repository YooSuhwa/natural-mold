"""PostgreSQL transaction and RLS contracts for durable chat notifications."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.authz.org_context import OrgContext
from app.config import settings
from app.database import SessionTenant, tenant_session
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.notification import Notification
from app.services import chat_notification_outbox as outbox
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.authz.unit_scope_fixture import ensure_unit_scope

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
async def completed_run(lab: RlsLab) -> AsyncIterator[tuple[OrgContext, UUID, UUID]]:
    engine = create_async_engine(async_url(lab.admin_url))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            uid = uuid4()
            ctx = await ensure_unit_scope(
                db, CurrentUser(id=uid, email=f"{uid.hex}@fixture.test", name="Task owner")
            )
            model = Model(provider="openai", model_name=uid.hex, display_name="Test model")
            db.add(model)
            await db.flush()
            agent = Agent(
                tenant_id=ctx.tenant_id,
                org_id=ctx.org_id,
                user_id=uid,
                name="Task",
                system_prompt="Test",
                model_id=model.id,
            )
            db.add(agent)
            await db.flush()
            conversation = Conversation(
                tenant_id=ctx.tenant_id,
                org_id=ctx.org_id,
                user_id=uid,
                agent_id=agent.id,
                agent_name_snapshot=agent.name,
                source="independent_job",
                last_activity_at=datetime.now(UTC),
            )
            db.add(conversation)
            await db.flush()
            run = ConversationRun(
                tenant_id=ctx.tenant_id,
                org_id=ctx.org_id,
                user_id=uid,
                conversation_id=conversation.id,
                agent_id=agent.id,
                status="completed",
                source="chat",
            )
            db.add(run)
            await db.commit()
            try:
                yield ctx, conversation.id, run.id
            finally:
                await db.execute(delete(Conversation).where(Conversation.id == conversation.id))
                await db.commit()
    finally:
        await engine.dispose()


async def test_contending_completion_producers_store_one_company_scoped_intent(
    sessions: Sessions,
    completed_run: tuple[OrgContext, UUID, UUID],
    lab: RlsLab,
) -> None:
    ctx, conversation_id, run_id = completed_run
    job_id = uuid4()

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
    completed_run: tuple[OrgContext, UUID, UUID],
) -> None:
    ctx, conversation_id, run_id = completed_run
    job_id = uuid4()
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
