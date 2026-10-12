from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryEvent,
    ConversationHistoryManifest,
)
from app.schemas.conversation_history import (
    ConversationHistoryCheckpointResponse,
    ConversationHistoryEventResponse,
    ConversationHistoryManifestResponse,
    JsonValue,
)
from app.schemas.conversation_history_protocol import HistoryRequest
from app.services.conversation_protocol_history import (
    LiveThreadHistoryRequest,
    load_live_thread_history,
)
from app.services.conversation_protocol_replay import (
    LiveProtocolEventsRequest,
    load_live_protocol_events,
)

_JSON = TypeAdapter(JsonValue)


@dataclass(frozen=True, slots=True)
class ConversationHistoryReadRequest:
    db: AsyncSession
    conversation_id: UUID
    user: CurrentUser
    context: OrgContext


@dataclass(frozen=True, slots=True)
class ConversationHistoryContent:
    manifest: ConversationHistoryManifestResponse | None
    messages: list[JsonValue]
    branches: list[JsonValue]
    state: dict[str, JsonValue]
    trace: dict[str, JsonValue]
    checkpoints: list[JsonValue]
    events: list[ConversationHistoryEventResponse]


async def load_conversation_history_content(
    request: ConversationHistoryReadRequest,
    conversation: Conversation,
) -> ConversationHistoryContent:
    if conversation.agent_lifecycle_state != "purged":
        raw_checkpoints = await load_live_thread_history(
            LiveThreadHistoryRequest(
                conversation,
                HistoryRequest(limit=100),
                request.context,
                request.db,
                request.user,
            )
        )
        checkpoints = [_JSON.validate_python(row) for row in raw_checkpoints]
        current = checkpoints[0] if checkpoints else {}
        current_map = current if isinstance(current, dict) else {}
        raw_values = current_map.get("values", {})
        values = raw_values if isinstance(raw_values, dict) else {}
        raw_messages = values.get("messages", [])
        messages = raw_messages if isinstance(raw_messages, list) else []
        raw_events = await load_live_protocol_events(
            LiveProtocolEventsRequest(
                request.db,
                conversation,
                request.context,
            )
        )
        events = [dict(item) for item in raw_events]
        safe_events = _JSON.validate_python(events)
        return ConversationHistoryContent(
            None,
            messages,
            checkpoints,
            {key: value for key, value in values.items() if key != "messages"},
            {"events": safe_events},
            checkpoints,
            [],
        )

    return await read_purged_history_content(request.db, conversation)


async def read_purged_history_content(
    db: AsyncSession,
    conversation: Conversation,
) -> ConversationHistoryContent:
    """Read the complete sanitized manifest without hydrating a deleted Agent."""
    manifest = await db.scalar(
        select(ConversationHistoryManifest).where(
            ConversationHistoryManifest.conversation_id == conversation.id,
            ConversationHistoryManifest.schema_version == 1,
            ConversationHistoryManifest.state == "complete",
        )
    )
    if manifest is None:
        raise ConflictError(
            "CONVERSATION_HISTORY_INCOMPLETE",
            "Purged conversation has no complete retained history manifest",
        )
    checkpoints = list(
        await db.scalars(
            select(ConversationHistoryCheckpoint)
            .where(ConversationHistoryCheckpoint.manifest_id == manifest.id)
            .order_by(
                ConversationHistoryCheckpoint.checkpoint_namespace,
                ConversationHistoryCheckpoint.checkpoint_id,
            )
        )
    )
    try:
        retained_checkpoints = [
            ConversationHistoryCheckpointResponse.model_validate(row) for row in checkpoints
        ]
    except ValidationError as exc:
        raise ConflictError(
            "CONVERSATION_HISTORY_INVALID",
            "Retained checkpoint does not match the history contract",
        ) from exc
    events = list(
        await db.scalars(
            select(ConversationHistoryEvent)
            .where(ConversationHistoryEvent.manifest_id == manifest.id)
            .order_by(
                ConversationHistoryEvent.assistant_message_id,
                ConversationHistoryEvent.sequence_start,
            )
        )
    )
    active = next((row for row in retained_checkpoints if row.branch.get("active") is True), None)
    current = active or (retained_checkpoints[-1] if retained_checkpoints else None)
    checkpoint_payloads: list[JsonValue] = [
        row.model_dump(mode="json") for row in retained_checkpoints
    ]
    return ConversationHistoryContent(
        ConversationHistoryManifestResponse.model_validate(manifest),
        list(current.messages) if current is not None else [],
        [row.branch for row in retained_checkpoints],
        dict(current.channel_values) if current is not None else {},
        dict(current.trace) if current is not None else {},
        checkpoint_payloads,
        [ConversationHistoryEventResponse.model_validate(row) for row in events],
    )


__all__ = [
    "ConversationHistoryContent",
    "ConversationHistoryReadRequest",
    "load_conversation_history_content",
    "read_purged_history_content",
]
