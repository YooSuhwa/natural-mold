"""The chat runtime's steer storage contract and its reserved stream event."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.authz.org_context import OrgContext

if TYPE_CHECKING:
    from app.models.conversation_run_steer import ConversationRunSteer


class SteerReceiptFeed(Protocol):
    async def changed_events(
        self, *, first_seq: int, force: bool = False
    ) -> list[StoredProtocolEvent]: ...


class ChatSteerStore(Protocol):
    """Durable operations provided only when hydrating a private root chat."""

    async def claim(
        self,
        db: AsyncSession,
        *,
        conversation_id: UUID,
        run_id: UUID,
        checkpointed_ids: Collection[UUID],
    ) -> Sequence[ConversationRunSteer]: ...

    async def confirm(
        self, db: AsyncSession, *, conversation_id: UUID, run_id: UUID, steer_ids: Collection[UUID]
    ) -> None: ...

    def receipt_feed(
        self, context: OrgContext, thread_id: str, run_id: str | None, *, source: str, profile: str
    ) -> SteerReceiptFeed | None: ...


def uses_steer_channel(event: StoredProtocolEvent) -> bool:
    if event["method"] != "custom" or not isinstance(event["data"], dict):
        return False
    names = (event_names.RUN_STEER, f"custom:{event_names.RUN_STEER}")
    data = event["data"]
    return data.get("name") in names or data.get("channel") in names


def valid_stored_steer(event: StoredProtocolEvent, *, conversation_id: UUID, run_id: str) -> bool:
    """Only a canonical receipt can occupy the reserved channel in historical traces."""
    data = event["data"]
    if event["namespace"] or data.keys() != {"name", "payload"}:
        return False
    payload = data["payload"]
    if data["name"] != event_names.RUN_STEER or not isinstance(payload, dict):
        return False
    if payload.keys() != {"conversation_id", "run_id", "steer_id", "status"}:
        return False
    steer_id = payload["steer_id"]
    if not isinstance(steer_id, str) or payload["status"] not in (
        "pending",
        "applied",
        "queued",
        "needs_review",
    ):
        return False
    try:
        if str(UUID(steer_id)) != steer_id:
            return False
    except ValueError:
        return False
    return (
        payload["conversation_id"] == event["thread_id"] == str(conversation_id)
        and payload["run_id"] == event["run_id"] == run_id
        and event["id"] == event["upstream_event_id"] == f"{steer_id}:steer:{payload['status']}"
    )
