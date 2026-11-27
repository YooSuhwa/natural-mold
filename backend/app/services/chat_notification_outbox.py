"""Enqueue a committed work transition and deliver its notice in a later transaction."""

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.notifications import Target, notify
from app.config import settings
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Transition:
    kind: str
    source_id: UUID
    tenant_id: UUID
    org_id: UUID
    user_id: UUID
    conversation_id: UUID
    run_id: UUID
    artifact_id: UUID | None
    version_id: UUID | None
    goal_id: UUID | None = None
    goal_version: int | None = None
    goal_revision: int | None = None


async def _enqueue(
    db: AsyncSession, intent: _Transition, *, admitted_with_notice: bool = False
) -> None:
    if not settings.chat_work_notifications_enabled and not admitted_with_notice:
        return

    # Producers run in the transaction that owns the transition. Never persist a
    # recipient/resource pair from another scope, including when RLS is disabled.
    source = (
        await db.execute(
            select(Conversation.source, ConversationRun.status)
            .select_from(ConversationRun)
            .join(Conversation, Conversation.id == ConversationRun.conversation_id)
            .where(
                Conversation.id == intent.conversation_id,
                Conversation.tenant_id == intent.tenant_id,
                Conversation.org_id == intent.org_id,
                Conversation.user_id == intent.user_id,
                ConversationRun.id == intent.run_id,
                ConversationRun.tenant_id == intent.tenant_id,
                ConversationRun.org_id == intent.org_id,
                ConversationRun.user_id == intent.user_id,
            )
        )
    ).one_or_none()
    if source is None:
        raise ValueError("Work notification source does not match its recipient and conversation")
    # Jobs admitted before switch-off keep their existing completion promise.
    if not settings.chat_work_notifications_enabled and tuple(source) != (
        "independent_job",
        "completed",
    ):
        return
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(ChatNotificationOutbox).values(asdict(intent))
    await db.execute(
        statement.on_conflict_do_nothing(index_elements=["user_id", "kind", "source_id"])
    )


async def enqueue_independent_completion(
    db: AsyncSession,
    *,
    job_id: UUID,
    admitted_with_notice: bool = False,
    tenant_id: UUID,
    org_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    artifact_id: UUID | None = None,
    version_id: UUID | None = None,
) -> None:
    await _enqueue(
        db,
        _Transition(
            source_id=job_id,
            kind="chat_task.completed",
            tenant_id=tenant_id,
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            artifact_id=artifact_id,
            version_id=version_id,
        ),
        admitted_with_notice=admitted_with_notice,
    )


async def enqueue_goal_transition(
    db: AsyncSession,
    *,
    goal_intent_id: UUID,
    goal_id: UUID,
    version: int,
    revision: int,
    kind: Literal["completed", "blocked", "approval_requested"],
    tenant_id: UUID,
    org_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    artifact_id: UUID | None = None,
    version_id: UUID | None = None,
) -> None:
    await _enqueue(
        db,
        _Transition(
            source_id=goal_intent_id,
            goal_id=goal_id,
            goal_version=version,
            goal_revision=revision,
            kind=f"chat_goal.{kind}",
            tenant_id=tenant_id,
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            artifact_id=artifact_id,
            version_id=version_id,
        ),
    )


async def drain_chat_notification_outbox(
    db: AsyncSession, *, tenant_id: UUID, limit: int = 50
) -> int:
    """Process a due batch in the caller's tenant transaction, including after switch-off."""
    from app.services.chat_notification_access import resolve_work_destination

    if limit < 1:
        return 0
    now = datetime.now(UTC)
    rows = list(
        await db.scalars(
            select(ChatNotificationOutbox)
            .where(
                ChatNotificationOutbox.tenant_id == tenant_id,
                ChatNotificationOutbox.status == "pending",
                ChatNotificationOutbox.available_at <= now,
            )
            .order_by(ChatNotificationOutbox.available_at, ChatNotificationOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    )
    processed = 0
    for row in rows:
        row_id = row.id
        attempts = row.attempt_count + 1
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        queue_age = max(0, int((now - created).total_seconds()))
        # Persist the attempt in the outer transaction. On SQLite the conditional
        # claim also serializes workers (FOR UPDATE is ignored on that dialect).
        claimed = await db.scalar(
            update(ChatNotificationOutbox)
            .where(
                ChatNotificationOutbox.id == row_id,
                ChatNotificationOutbox.tenant_id == tenant_id,
                ChatNotificationOutbox.status == "pending",
                ChatNotificationOutbox.attempt_count == row.attempt_count,
            )
            .values(attempt_count=attempts)
            .returning(ChatNotificationOutbox.id)
        )
        if claimed is None:
            continue
        processed += 1
        try:
            async with db.begin_nested():
                notification = None
                if row.kind == "chat_goal.approval_requested":
                    from app.services.chat_notification_approval import publish_goal_approval

                    notification = await publish_goal_approval(db, row)
                elif destination := await resolve_work_destination(db, row):
                    link = None
                    if destination.agent_id:
                        link = f"/agents/{destination.agent_id}/conversations/{row.conversation_id}"
                        if row.goal_id:
                            link += "#conversation-goal"
                    [notification] = await notify(
                        db,
                        kind=row.kind,
                        target=Target("chat_work_transition", row_id),
                        recipients=(row.user_id,),
                        org_id=row.org_id,
                        tenant_id=tenant_id,
                        link=link,
                    )
                row.status = "delivered" if notification is not None else "suppressed"
                row.notification_id = notification.id if notification is not None else None
                row.finished_at = now
        except Exception as error:
            if isinstance(error, DBAPIError) and getattr(error.orig, "sqlstate", None) == "55P03":
                # The concurrent editor owns the parent. A second outbox UPDATE in
                # this transaction can trigger another FK check against that same
                # row. Its attempt is already recorded; retry on the next sweep.
                logger.info(
                    "chat_notice_busy id=%s attempt=%d queue_age_seconds=%d",
                    row_id,
                    attempts,
                    queue_age,
                )
                continue
            # The savepoint rolled back the bell write, including failures after
            # notify() flushed. A later attempt can safely create it once.
            retry_delay = min(5 * (2 ** min(attempts - 1, 6)), 300)
            await db.execute(
                update(ChatNotificationOutbox)
                .where(
                    ChatNotificationOutbox.id == row_id,
                    ChatNotificationOutbox.tenant_id == tenant_id,
                )
                .values(available_at=now + timedelta(seconds=retry_delay))
                .execution_options(synchronize_session=False)
            )
            logger.warning(
                "chat_notice_retry id=%s attempt=%d queue_age_seconds=%d "
                "retry_seconds=%d error_type=%s",
                row_id,
                attempts,
                queue_age,
                retry_delay,
                type(error).__name__,
            )
    return processed
