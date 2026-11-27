from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation_run import ConversationRun
from app.models.notification import Notification
from app.schemas.notification import ChatNotificationDestination
from app.services import chat_notification_access
from app.services import chat_notification_outbox as delivery
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent


class NoticeSource(TypedDict):
    user_id: UUID
    org_id: UUID
    tenant_id: UUID
    conversation_id: UUID
    run_id: UUID


@pytest.fixture
async def source(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> NoticeSource:
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    conversation_id = await seed_conversation_with_agent()
    run = ConversationRun(
        conversation_id=conversation_id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        status="completed",
        source="ui",
    )
    db.add(run)
    await db.commit()
    destination = ChatNotificationDestination(
        conversation_id=conversation_id,
        agent_id=uuid4(),
        run_id=run.id,
        goal_id=None,
        job_id=None,
        artifact_id=None,
        version_id=None,
        filename=None,
    )
    monkeypatch.setattr(
        chat_notification_access, "resolve_work_destination", AsyncMock(return_value=destination)
    )
    return {
        "user_id": TEST_USER_ID,
        "org_id": TEST_ORG_ID,
        "tenant_id": TEST_TENANT_ID,
        "conversation_id": conversation_id,
        "run_id": run.id,
    }


async def test_retry_after_bell_deletion_never_reopens_or_recreates(
    source: NoticeSource,
    db: AsyncSession,
) -> None:
    job_id = uuid4()
    await delivery.enqueue_independent_completion(db, **source, job_id=job_id)
    await delivery.enqueue_independent_completion(db, **source, job_id=job_id)
    await db.commit()
    assert not await delivery.drain_chat_notification_outbox(db, tenant_id=uuid4())
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID) == 1
    await db.commit()
    notification = (await db.scalars(select(Notification))).one()
    assert (notification.actor_id, notification.target_type) == (None, "chat_work_transition")
    notification.read_at = datetime.now(UTC)
    await db.commit()
    await delivery.enqueue_independent_completion(db, **source, job_id=job_id)
    assert not await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID)
    assert notification.read_at
    await db.delete(notification)
    await db.commit()
    await delivery.enqueue_independent_completion(db, **source, job_id=job_id)
    assert not await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID)
    assert await db.scalar(select(func.count()).select_from(Notification)) == 0


async def test_new_goal_block_transition_has_its_own_notice_in_same_hour(
    source: NoticeSource,
    db: AsyncSession,
) -> None:
    goal_id = uuid4()
    for revision in (1, 2):
        transition = uuid4()
        for _ in range(2):
            await delivery.enqueue_goal_transition(
                db,
                **source,
                goal_id=goal_id,
                goal_intent_id=transition,
                kind="blocked",
                version=1,
                revision=revision,
            )
    await db.commit()
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID) == 2
    rows = list(await db.scalars(select(Notification)))
    assert {row.kind for row in rows} == {"chat_goal.blocked"}
    assert len({row.target_id for row in rows}) == 2
    assert all(row.actor_id is None and not row.payload.get("target_name") for row in rows)


async def test_switch_off_stops_producers_but_drains_already_committed_intents(
    source: NoticeSource,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await delivery.enqueue_independent_completion(db, **source, job_id=uuid4())
    await db.commit()
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", False)
    await delivery.enqueue_independent_completion(db, **source, job_id=uuid4())
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID) == 1
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(ChatNotificationOutbox)) == 1


async def test_withdrawn_access_suppresses_without_writing_a_bell(
    source: NoticeSource,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat_notification_access, "resolve_work_destination", AsyncMock(return_value=None)
    )
    await delivery.enqueue_independent_completion(db, **source, job_id=uuid4())
    await db.commit()
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID) == 1
    await db.commit()
    row = (await db.scalars(select(ChatNotificationOutbox))).one()
    assert (row.status, row.attempt_count) == ("suppressed", 1)
    assert not await db.scalar(select(Notification.id))


@pytest.mark.parametrize("key", ["tenant_id", "org_id", "user_id", "run_id", "conversation_id"])
async def test_foreign_source_or_owner_never_enqueues(
    source: NoticeSource,
    db: AsyncSession,
    key: Literal["tenant_id", "org_id", "user_id", "run_id", "conversation_id"],
) -> None:
    mismatched = source.copy()
    mismatched[key] = uuid4()
    with pytest.raises(ValueError, match="does not match"):
        await delivery.enqueue_independent_completion(db, **mismatched, job_id=uuid4())
    assert await db.scalar(select(func.count()).select_from(ChatNotificationOutbox)) == 0


async def test_retry_after_notice_flush_rolls_back_notice_and_preserves_other_rows(
    source: NoticeSource,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for _ in range(2):
        await delivery.enqueue_independent_completion(db, **source, job_id=uuid4())
    await db.commit()
    real_notify = delivery.notify
    writes = 0

    async def fail_first_write(*args, **kwargs):
        nonlocal writes
        result = await real_notify(*args, **kwargs)
        writes += 1
        if writes == 1:
            raise RuntimeError("simulated publisher failure after flush")
        return result

    monkeypatch.setattr(delivery, "notify", fail_first_write)
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID) == 2
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(Notification)) == 1
    assert not await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID)
    pending = (
        await db.scalars(
            select(ChatNotificationOutbox).where(ChatNotificationOutbox.status == "pending")
        )
    ).one()
    assert pending.attempt_count == 1 and pending.notification_id is None
    await db.execute(
        update(ChatNotificationOutbox)
        .where(ChatNotificationOutbox.id == pending.id)
        .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID) == 1
    await db.rollback()  # simulated process stop before the transaction commits
    assert await db.scalar(select(func.count()).select_from(Notification)) == 1
    await db.execute(
        update(ChatNotificationOutbox)
        .where(ChatNotificationOutbox.status == "pending")
        .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID) == 1
    await db.commit()
    assert not await delivery.drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID)
    assert sorted(await db.scalars(select(ChatNotificationOutbox.attempt_count))) == [1, 2]
    assert await db.scalar(select(func.count()).select_from(Notification)) == 2
