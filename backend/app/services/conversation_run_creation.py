"""Conversation run creation and state machine extracted from ``conversation_run_service``.

Task 10 I4: ``create_run`` plus the run status vocabulary, transition, cancel-intent,
and heartbeat writes, moved unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    lock_run_conversation,
    private_run_predicate,
)
from app.authz.org_context import OrgContext
from app.exceptions import ConflictError
from app.models.conversation_run import RUN_ACTIVE_STATUSES, RUN_TERMINAL_STATUSES, ConversationRun
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services.conversation_run_envelope_projection import project_owned_envelopes
from app.services.conversation_runtime_policy import (
    copy_snapshot_to_run,
    ensure_conversation_runtime_policy,
    require_parent_snapshot_match,
)


def run_service_attr(name: str) -> Any:
    """Return ``conversation_run_service.<name>`` as currently bound."""

    from app.services import conversation_run_service

    return getattr(conversation_run_service, name)


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


async def create_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    context: OrgContext,
    source: RunSource,
    input_preview: str | None,
    parent_run_id: uuid.UUID | None = None,
    interrupt_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    allow_legacy_resume: bool = False,
) -> ConversationRun:
    conversation = await lock_run_conversation(
        db, RunConversationTarget(conversation_id, agent_id, context)
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        _, _, runtime_policy = await ensure_conversation_runtime_policy(
            db, ConversationPolicyTarget(conversation.id, context)
        )
    except RuntimePolicySnapshotError as exc:
        raise ConflictError(exc.code, exc.code) from None

    active = await run_service_attr("get_active_run")(
        db, conversation_id=conversation_id, context=context
    )
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
                    private_run_predicate(context),
                    ConversationRun.source == "resume",
                    ConversationRun.parent_run_id.is_(None),
                    ConversationRun.interrupt_id == interrupt_id,
                )
                .limit(1)
            )
            if existing_legacy_resume.scalar_one_or_none() is not None:
                raise _conflict("Legacy interrupted run was already resumed")
        else:
            latest = await run_service_attr("_latest_interrupted_run")(
                db,
                conversation_id=conversation_id,
                context=context,
            )
            if latest is None or latest.id != parent_run_id:
                raise _conflict("Resume run must target the latest interrupted run")
            if latest.interrupt_id and latest.interrupt_id != interrupt_id:
                raise _conflict("Resume interrupt id does not match the interrupted run")
            try:
                require_parent_snapshot_match(latest, runtime_policy)
            except RuntimePolicySnapshotError as exc:
                raise ConflictError(exc.code, exc.code) from None

    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=agent_id,
        user_id=context.user_id,
        parent_run_id=parent_run_id,
        source=source,
        status="queued",
        is_active=True,
        interrupt_id=interrupt_id if source == "resume" else None,
        input_preview=(input_preview[:500] if input_preview else None),
        metadata_json=metadata,
    )
    copy_snapshot_to_run(run, runtime_policy)
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
    cancellation_ack_worker_id: str | None = None,
    allow_workerless_cancellation_ack: bool = False,
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

    if status in RUN_TERMINAL_STATUSES and run.cancel_requested_at is not None:
        owner_ack = (
            cancellation_ack_worker_id is not None
            and run.worker_instance_id == cancellation_ack_worker_id
        )
        workerless_ack = allow_workerless_cancellation_ack and run.worker_instance_id is None
        if not (owner_ack or workerless_ack) and (
            cancellation_ack_worker_id is not None or allow_workerless_cancellation_ack
        ):
            raise ValueError("Cancellation acknowledgment requires the owning worker")

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
        if run.cancel_requested_at is not None:
            owner_ack = (
                cancellation_ack_worker_id is not None
                and run.worker_instance_id == cancellation_ack_worker_id
            )
            workerless_ack = allow_workerless_cancellation_ack and run.worker_instance_id is None
            if owner_ack or workerless_ack:
                run.cancellation_acknowledged_at = run.cancellation_acknowledged_at or now
        if status == "interrupted" and interrupt_id:
            run.interrupt_id = interrupt_id
        if status in {"failed", "stale"}:
            run.error_code = error_code
            run.error_message = error_message

    await project_owned_envelopes(db, run)
    await db.flush()
    return run


async def request_cancel_run(
    db: AsyncSession,
    run: ConversationRun,
    *,
    reason: str = "stop",
) -> ConversationRun:
    if run.status in RUN_TERMINAL_STATUSES or run.status == "canceling":
        return run
    if run.status not in {"queued", "running"}:
        raise ValueError(f"Run cannot be canceled from status: {run.status}")
    run.cancel_reason = reason
    return await transition_run(db, run, "canceling")


async def heartbeat_run(db: AsyncSession, run_id: uuid.UUID) -> bool:
    run = await db.get(ConversationRun, run_id, with_for_update=True)
    if run is None or not run.is_active or run.status not in RUN_ACTIVE_STATUSES:
        return False
    run.heartbeat_at = utc_now_naive()
    await db.flush()
    return True
