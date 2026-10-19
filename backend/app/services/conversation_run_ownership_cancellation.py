"""Durable cancel-intent staging and post-commit local notification."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, noload

from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation_run import RUN_TERMINAL_STATUSES, ConversationRun
from app.services import conversation_run_service


@dataclass(frozen=True, slots=True)
class CancelIntentRequest:
    run_id: uuid.UUID
    reason: str


@dataclass(frozen=True, slots=True)
class ParticipantCancelRequest:
    agent_id_snapshot: uuid.UUID
    reason: str


@dataclass(frozen=True, slots=True)
class PreparedCancelIntent:
    run_id: uuid.UUID
    status: str
    worker_instance_id: str | None
    reason: str


class LocalRunRegistry(Protocol):
    worker_instance_id: str

    def request_cancel(self, run_id: uuid.UUID, *, reason: Literal["stop"]) -> bool: ...


async def stage_cancel_intent(
    db: AsyncSession,
    request: CancelIntentRequest,
) -> PreparedCancelIntent:
    """Stage one cancel intent without committing or notifying process-local state."""
    run = await db.scalar(
        select(ConversationRun)
        .options(
            load_only(
                ConversationRun.id,
                ConversationRun.status,
                ConversationRun.is_active,
                ConversationRun.worker_instance_id,
                ConversationRun.cancel_requested_at,
                ConversationRun.cancel_reason,
                raiseload=True,
            ),
            noload(ConversationRun.metrics),
        )
        .where(ConversationRun.id == request.run_id)
        .with_for_update()
        .execution_options(populate_existing=True, autoflush=False)
    )
    if run is None:
        raise ValueError("Conversation run not found")
    if run.status not in RUN_TERMINAL_STATUSES:
        await conversation_run_service.request_cancel_run(db, run, reason=request.reason)
    return PreparedCancelIntent(run.id, run.status, run.worker_instance_id, request.reason)


async def stage_participant_cancel_intents(
    db: AsyncSession,
    request: ParticipantCancelRequest,
) -> tuple[PreparedCancelIntent, ...]:
    """Cancel only runs where the target Agent was an accepted participant."""
    run_ids = tuple(
        await db.scalars(
            select(ConversationRunAgent.run_id)
            .join(ConversationRun, ConversationRun.id == ConversationRunAgent.run_id)
            .where(
                ConversationRunAgent.agent_id_snapshot == request.agent_id_snapshot,
                ConversationRun.status.in_(("queued", "running", "canceling")),
            )
            .order_by(ConversationRunAgent.run_id)
        )
    )
    intents: list[PreparedCancelIntent] = []
    for run_id in run_ids:
        intents.append(await stage_cancel_intent(db, CancelIntentRequest(run_id, request.reason)))
    return tuple(intents)


async def persist_cancel_intent(
    session_factory: Callable[[], AsyncSession],
    request: CancelIntentRequest,
) -> PreparedCancelIntent:
    async with session_factory() as db:
        intent = await stage_cancel_intent(db, request)
        await db.commit()
        return intent


def notify_local_owner(intent: PreparedCancelIntent, registry: LocalRunRegistry) -> bool:
    """Notify only the matching process owner; this function never writes an ACK."""
    if intent.status != "canceling" or intent.worker_instance_id != registry.worker_instance_id:
        return False
    return registry.request_cancel(intent.run_id, reason="stop")


async def participant_run_conflicts(
    db: AsyncSession,
    agent_id_snapshot: uuid.UUID,
) -> tuple[uuid.UUID, ...]:
    """Return active or requested-without-ACK runs that keep lifecycle stopping."""
    return tuple(
        await db.scalars(
            select(ConversationRun.id)
            .join(ConversationRunAgent, ConversationRunAgent.run_id == ConversationRun.id)
            .where(
                ConversationRunAgent.agent_id_snapshot == agent_id_snapshot,
                (
                    ConversationRun.is_active.is_(True)
                    | (
                        ConversationRun.cancel_requested_at.is_not(None)
                        & ConversationRun.cancellation_acknowledged_at.is_(None)
                    )
                ),
            )
            .order_by(ConversationRun.id)
        )
    )


__all__ = [
    "CancelIntentRequest",
    "ParticipantCancelRequest",
    "PreparedCancelIntent",
    "notify_local_owner",
    "participant_run_conflicts",
    "persist_cancel_intent",
    "stage_cancel_intent",
    "stage_participant_cancel_intents",
]
