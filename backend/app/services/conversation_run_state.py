"""Conversation run state transitions, cancel intent and heartbeat (Task 10 I4/I9).

Moved unchanged from ``conversation_run_creation``; re-exported there and by
``conversation_run_service``.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run import RUN_ACTIVE_STATUSES, RUN_TERMINAL_STATUSES, ConversationRun
from app.services.conversation_run_creation import ALLOWED_TRANSITIONS, RunStatus, utc_now_naive
from app.services.conversation_run_envelope_projection import project_owned_envelopes


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
