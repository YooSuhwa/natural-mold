"""Compatibility projections from durable ConversationRun state."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, noload

from app.models.agent_api import AgentApiRun
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun


@dataclass(frozen=True, slots=True)
class UnlinkedEnvelopeConflicts:
    api_run_ids: tuple[uuid.UUID, ...]
    trigger_run_ids: tuple[uuid.UUID, ...]

    @property
    def exists(self) -> bool:
        return bool(self.api_run_ids or self.trigger_run_ids)


def api_status_for_run(status: str) -> str:
    return {
        "queued": "pending",
        "running": "running",
        "canceling": "canceling",
        "completed": "succeeded",
        "canceled": "canceled",
    }.get(status, "failed")


def trigger_status_for_run(status: str) -> str:
    return {
        "queued": "queued",
        "running": "running",
        "canceling": "canceling",
        "completed": "success",
        "canceled": "canceled",
    }.get(status, "failed")


async def project_owned_envelopes(db: AsyncSession, run: ConversationRun) -> None:
    api_run = await db.scalar(
        select(AgentApiRun)
        .options(
            load_only(
                AgentApiRun.id,
                AgentApiRun.conversation_run_id,
                AgentApiRun.status,
                raiseload=True,
            ),
            noload(AgentApiRun.deployment),
            noload(AgentApiRun.thread),
        )
        .where(AgentApiRun.conversation_run_id == run.id)
        .execution_options(populate_existing=True, autoflush=False)
    )
    trigger_run = await db.scalar(
        select(AgentTriggerRun)
        .options(
            load_only(
                AgentTriggerRun.id,
                AgentTriggerRun.conversation_run_id,
                AgentTriggerRun.status,
                raiseload=True,
            ),
            noload(AgentTriggerRun.trigger),
            noload(AgentTriggerRun.conversation),
        )
        .where(AgentTriggerRun.conversation_run_id == run.id)
        .execution_options(populate_existing=True, autoflush=False)
    )
    if api_run is not None:
        api_run.status = api_status_for_run(run.status)
    if trigger_run is not None:
        trigger_run.status = trigger_status_for_run(run.status)


async def active_unlinked_envelopes(
    db: AsyncSession,
    agent_ids: Sequence[uuid.UUID],
) -> UnlinkedEnvelopeConflicts:
    if not agent_ids:
        return UnlinkedEnvelopeConflicts((), ())
    api_ids = tuple(
        await db.scalars(
            select(AgentApiRun.id)
            .join(Conversation, Conversation.id == AgentApiRun.conversation_id)
            .where(
                Conversation.agent_id.in_(agent_ids),
                AgentApiRun.conversation_run_id.is_(None),
                AgentApiRun.status.in_(("pending", "running", "canceling")),
            )
            .order_by(AgentApiRun.id)
        )
    )
    trigger_ids = tuple(
        await db.scalars(
            select(AgentTriggerRun.id)
            .where(
                AgentTriggerRun.agent_id.in_(agent_ids),
                AgentTriggerRun.conversation_run_id.is_(None),
                AgentTriggerRun.status.in_(("queued", "running", "canceling")),
            )
            .order_by(AgentTriggerRun.id)
        )
    )
    return UnlinkedEnvelopeConflicts(api_ids, trigger_ids)


__all__ = [
    "UnlinkedEnvelopeConflicts",
    "active_unlinked_envelopes",
    "api_status_for_run",
    "project_owned_envelopes",
    "trigger_status_for_run",
]
