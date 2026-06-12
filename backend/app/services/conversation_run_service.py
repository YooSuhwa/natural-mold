from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.agent_runtime import event_names
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import RUN_ACTIVE_STATUSES, RUN_TERMINAL_STATUSES, ConversationRun
from app.services import trace_storage
from app.services.artifact_service import finalize_artifacts_for_run
from app.services.conversation_audit_service import record_conversation_run_audit

logger = logging.getLogger(__name__)

RunSource = Literal["chat", "start", "edit", "regenerate", "resume"]
RunStatus = Literal[
    "queued",
    "running",
    "interrupted",
    "canceling",
    "canceled",
    "completed",
    "failed",
    "stale",
]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "failed", "stale", "canceling"},
    "running": {"completed", "failed", "interrupted", "canceling", "stale"},
    "canceling": {"canceled", "failed", "stale"},
    "completed": set(),
    "failed": set(),
    "interrupted": set(),
    "canceled": set(),
    "stale": set(),
}


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)


def _is_active_run_unique_violation(exc: IntegrityError) -> bool:
    text = f"{exc} {getattr(exc, 'orig', '')}".lower()
    return "uq_conversation_runs_active_conversation" in text or (
        "unique constraint failed" in text and "conversation_runs.conversation_id" in text
    )


async def _conversation_owned_by_user(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation | None:
    result = await db.execute(
        select(Conversation)
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(
            Conversation.id == conversation_id,
            Conversation.agent_id == agent_id,
            Agent.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _latest_interrupted_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ConversationRun | None:
    resume_child = aliased(ConversationRun)
    resume_child_exists = (
        select(resume_child.id)
        .where(
            resume_child.parent_run_id == ConversationRun.id,
            resume_child.source == "resume",
        )
        .exists()
    )
    result = await db.execute(
        select(ConversationRun)
        .where(
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.user_id == user_id,
            ConversationRun.status == "interrupted",
            ~resume_child_exists,
        )
        .order_by(ConversationRun.completed_at.desc(), ConversationRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_interrupted_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ConversationRun | None:
    return await _latest_interrupted_run(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
    )


async def get_active_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ConversationRun | None:
    result = await db.execute(
        select(ConversationRun).where(
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.user_id == user_id,
            ConversationRun.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def active_runs_for_conversations(
    db: AsyncSession,
    conversation_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, ConversationRun]:
    if not conversation_ids:
        return {}
    resume_child = aliased(ConversationRun)
    resume_child_exists = (
        select(resume_child.id)
        .where(
            resume_child.parent_run_id == ConversationRun.id,
            resume_child.source == "resume",
        )
        .exists()
    )
    result = await db.execute(
        select(ConversationRun)
        .where(
            ConversationRun.conversation_id.in_(conversation_ids),
            or_(
                ConversationRun.is_active.is_(True),
                ((ConversationRun.status == "interrupted") & (~resume_child_exists)),
            ),
        )
        .order_by(
            ConversationRun.conversation_id,
            ConversationRun.is_active.desc(),
            ConversationRun.created_at.desc(),
        )
    )
    runs: dict[uuid.UUID, ConversationRun] = {}
    for run in result.scalars().all():
        runs.setdefault(run.conversation_id, run)
    return runs


async def current_run_for_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
) -> ConversationRun | None:
    return (await active_runs_for_conversations(db, [conversation_id])).get(conversation_id)


async def latest_run_for_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
) -> ConversationRun | None:
    """최신 run을 상태와 무관하게 1건 반환.

    ``active_run`` 은 active/미해결 interrupted 만 보고하므로 canceled 처럼
    terminal 로 끝난 마지막 turn 의 상태를 알 수 없다. 메시지는 checkpointer
    파생이라 run_id(uuid4) ↔ message id(uuid5) 매칭이 불가능해, "마지막 turn
    이 취소되었다" 는 사실은 conversation 단위 최신 run 으로만 durable 하게
    전달할 수 있다 (``ix_conversation_runs_conversation_created`` 인덱스 사용).
    """
    result = await db.execute(
        select(ConversationRun)
        .where(ConversationRun.conversation_id == conversation_id)
        .order_by(ConversationRun.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def get_run_for_user(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    for_update: bool = False,
) -> ConversationRun | None:
    """``for_update=True`` 는 이후 ``transition_run`` 으로 상태를 바꿀 호출자용.

    Postgres 에서 row lock 으로 worker/heartbeat 와의 read-modify-write 경합을
    직렬화한다 (SQLite dialect 는 FOR UPDATE 를 무시 — 테스트는 단일 루프라 안전).
    """
    stmt = select(ConversationRun).where(
        ConversationRun.id == run_id,
        ConversationRun.conversation_id == conversation_id,
        ConversationRun.user_id == user_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    source: RunSource,
    input_preview: str | None,
    parent_run_id: uuid.UUID | None = None,
    interrupt_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    allow_legacy_resume: bool = False,
) -> ConversationRun:
    conversation = await _conversation_owned_by_user(
        db,
        conversation_id=conversation_id,
        agent_id=agent_id,
        user_id=user_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    active = await get_active_run(db, conversation_id=conversation_id, user_id=user_id)
    if active is not None:
        raise _conflict("Conversation already has an active run")

    if source == "resume":
        if parent_run_id is None:
            if not allow_legacy_resume or not interrupt_id:
                raise _conflict("Resume run requires an interrupted parent run")
            existing_legacy_resume = await db.execute(
                select(ConversationRun)
                .where(
                    ConversationRun.conversation_id == conversation_id,
                    ConversationRun.user_id == user_id,
                    ConversationRun.source == "resume",
                    ConversationRun.parent_run_id.is_(None),
                    ConversationRun.interrupt_id == interrupt_id,
                )
                .limit(1)
            )
            if existing_legacy_resume.scalar_one_or_none() is not None:
                raise _conflict("Legacy interrupted run was already resumed")
        else:
            latest = await _latest_interrupted_run(
                db,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if latest is None or latest.id != parent_run_id:
                raise _conflict("Resume run must target the latest interrupted run")
            if latest.interrupt_id and latest.interrupt_id != interrupt_id:
                raise _conflict("Resume interrupt id does not match the interrupted run")

    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=agent_id,
        user_id=user_id,
        parent_run_id=parent_run_id,
        source=source,
        status="queued",
        is_active=True,
        interrupt_id=interrupt_id if source == "resume" else None,
        input_preview=(input_preview[:500] if input_preview else None),
        metadata_json=metadata,
    )
    db.add(run)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_active_run_unique_violation(exc):
            await db.rollback()
            raise _conflict("Conversation already has an active run") from exc
        raise
    return run


async def transition_run(
    db: AsyncSession,
    run: ConversationRun,
    status: RunStatus,
    *,
    worker_instance_id: str | None = None,
    interrupt_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    last_event_id: str | None = None,
) -> ConversationRun:
    """run 상태 전이의 단일 진입점.

    동시성 계약: 같은 row 를 갱신하는 경로(worker 전이, heartbeat, cancel API,
    stale sweep)가 서로 다른 세션에서 read-modify-write 하므로, 상태를 바꾸는
    호출자는 run 을 ``with_for_update`` 로 로드해 stale read 기반 lost update 를
    막아야 한다. 단일 프로세스/단일 asyncio 워커(현 기본 배포)에서는 트랜잭션이
    짧아 실질 경합이 드물지만, 멀티 워커 확장 시 이 계약이 필수가 된다.
    """
    if status not in ALLOWED_TRANSITIONS.get(run.status, set()):
        raise ValueError(f"Invalid run status transition: {run.status} -> {status}")

    now = utc_now_naive()
    run.status = status
    if last_event_id:
        run.last_event_id = last_event_id

    if status == "running":
        run.is_active = True
        run.started_at = run.started_at or now
        run.heartbeat_at = now
        if worker_instance_id:
            run.worker_instance_id = worker_instance_id
    elif status == "canceling":
        run.is_active = True
        run.cancel_requested_at = run.cancel_requested_at or now
    elif status in RUN_TERMINAL_STATUSES:
        run.is_active = False
        run.completed_at = run.completed_at or now
        if status == "interrupted" and interrupt_id:
            run.interrupt_id = interrupt_id
        if status in {"failed", "stale"}:
            run.error_code = error_code
            run.error_message = error_message

    await db.flush()
    return run


async def request_cancel_run(db: AsyncSession, run: ConversationRun) -> ConversationRun:
    if run.status in RUN_TERMINAL_STATUSES or run.status == "canceling":
        return run
    if run.status not in {"queued", "running"}:
        raise ValueError(f"Run cannot be canceled from status: {run.status}")
    return await transition_run(db, run, "canceling")


async def heartbeat_run(db: AsyncSession, run_id: uuid.UUID) -> bool:
    run = await db.get(ConversationRun, run_id, with_for_update=True)
    if run is None or not run.is_active or run.status not in RUN_ACTIVE_STATUSES:
        return False
    run.heartbeat_at = utc_now_naive()
    await db.flush()
    return True


async def finalize_run_outputs_for_status(
    db: AsyncSession,
    run: ConversationRun,
    status: RunStatus,
    *,
    append_terminal_event: bool = False,
) -> None:
    if status not in {"canceled", "stale", "failed"}:
        return
    if append_terminal_event and status == "canceled":
        event_id = f"{run.id}-canceled"
        await trace_storage.append_events(
            db,
            conversation_id=run.conversation_id,
            assistant_msg_id=str(run.id),
            events_chunk=[
                {
                    "id": event_id,
                    "event": event_names.MESSAGE_END,
                    "data": {"usage": {}, "content": "", "status": "canceled"},
                }
            ],
            status="streaming",
        )
        run.last_event_id = event_id
    await trace_storage.finalize_turn(
        db,
        assistant_msg_id=str(run.id),
        status="completed" if status == "canceled" else "failed",
        conversation_id=run.conversation_id,
    )
    await finalize_artifacts_for_run(
        db,
        conversation_id=run.conversation_id,
        assistant_msg_id=str(run.id),
        run_status=status,
    )


async def mark_stale_active_runs(
    db: AsyncSession,
    *,
    stale_before: datetime,
    worker_instance_id: str | None,
    include_workerless: bool,
    protected_run_ids: Sequence[uuid.UUID] | None = None,
    conversation_id: uuid.UUID | None = None,
) -> int:
    reference_time = func.coalesce(
        ConversationRun.heartbeat_at,
        ConversationRun.started_at,
        ConversationRun.created_at,
    )
    conditions = [
        ConversationRun.is_active.is_(True),
        ConversationRun.status.in_(RUN_ACTIVE_STATUSES),
        reference_time <= stale_before,
    ]
    if conversation_id is not None:
        conditions.append(ConversationRun.conversation_id == conversation_id)
    if protected_run_ids:
        conditions.append(ConversationRun.id.notin_(list(protected_run_ids)))
    if worker_instance_id is not None:
        ownership_conditions = [ConversationRun.worker_instance_id == worker_instance_id]
        if include_workerless:
            ownership_conditions.append(ConversationRun.worker_instance_id.is_(None))
        conditions.append(or_(*ownership_conditions))
    elif not include_workerless:
        conditions.append(ConversationRun.worker_instance_id.is_not(None))

    result = await db.execute(select(ConversationRun).where(*conditions).with_for_update())
    runs = list(result.scalars().all())
    marked = 0
    for run in runs:
        try:
            await transition_run(
                db,
                run,
                "stale",
                error_code="stale_heartbeat",
                error_message="Run heartbeat exceeded the stale threshold.",
            )
        except ValueError:
            # 다른 경로(worker finalize/cancel)가 먼저 terminal 전이를 끝낸 run.
            # 한 run 의 경합이 같은 배치의 나머지 sweep 을 막지 않도록 skip.
            logger.warning(
                "skipping stale sweep for run %s: already transitioned to %s",
                run.id,
                run.status,
            )
            continue
        await finalize_run_outputs_for_status(db, run, "stale")
        await record_conversation_run_audit(
            db,
            action="conversation.run_stale",
            run=run,
            status="stale",
        )
        marked += 1
    return marked


__all__ = [
    "ALLOWED_TRANSITIONS",
    "RUN_ACTIVE_STATUSES",
    "RUN_TERMINAL_STATUSES",
    "active_runs_for_conversations",
    "create_run",
    "current_run_for_conversation",
    "finalize_run_outputs_for_status",
    "get_active_run",
    "get_latest_interrupted_run",
    "get_run_for_user",
    "heartbeat_run",
    "latest_run_for_conversation",
    "mark_stale_active_runs",
    "request_cancel_run",
    "transition_run",
]
