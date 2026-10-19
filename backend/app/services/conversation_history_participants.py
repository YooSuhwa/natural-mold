from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.agent_lifecycle_locks import read_agent_tree


class HistoryParticipantClosureError(RuntimeError):
    """The complete runtime participant closure cannot be reconstructed."""


@dataclass(frozen=True, slots=True)
class HistoryParticipantRequest:
    db: AsyncSession
    agent_id: UUID
    conversation_id: UUID


async def load_history_participant_ids(request: HistoryParticipantRequest) -> tuple[UUID, ...]:
    """Load a conservative runtime closure without trusting removed archive links."""
    lifecycle_state = await request.db.scalar(
        select(Conversation.agent_lifecycle_state).where(
            Conversation.id == request.conversation_id,
            Conversation.agent_id == request.agent_id,
        )
    )
    if lifecycle_state is None:
        raise HistoryParticipantClosureError("conversation lifecycle source is unavailable")

    run_ids = tuple(
        await request.db.scalars(
            select(ConversationRun.id).where(
                ConversationRun.conversation_id == request.conversation_id
            )
        )
    )
    participant_rows = (
        (
            await request.db.execute(
                select(
                    ConversationRunAgent.run_id,
                    ConversationRunAgent.agent_id_snapshot,
                ).where(ConversationRunAgent.run_id.in_(run_ids))
            )
        ).all()
        if run_ids
        else []
    )
    by_run: dict[UUID, set[UUID]] = {run_id: set() for run_id in run_ids}
    for run_id, participant_id in participant_rows:
        by_run[run_id].add(participant_id)
    retained = tuple(
        sorted(
            {participant_id for participants in by_run.values() for participant_id in participants},
            key=lambda item: item.bytes,
        )
    )
    if lifecycle_state != "active":
        if not run_ids or any(
            not participants or request.agent_id not in participants
            for participants in by_run.values()
        ):
            raise HistoryParticipantClosureError(
                "archived conversation has no complete retained participant closure"
            )
        return retained

    current = await read_agent_tree(request.db, request.agent_id)
    if not current.members or request.agent_id not in current.agent_ids:
        raise HistoryParticipantClosureError("current Agent tree is unavailable")
    return tuple(sorted(set(retained).union(current.agent_ids), key=lambda item: item.bytes))


__all__ = [
    "HistoryParticipantClosureError",
    "HistoryParticipantRequest",
    "load_history_participant_ids",
]
