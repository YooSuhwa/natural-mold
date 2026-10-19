from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import StoredProtocolEvent, resequence_protocol_events
from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.schemas.conversation_history import JsonValue
from app.services import trace_storage
from app.services.chat import secrets as chat_secrets
from app.services.conversation_protocol_event_projection import (
    RawEventProjectionRequest,
    stored_events_from_raw,
)

_JSON_MAPPING = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True, slots=True)
class LiveProtocolEventsRequest:
    db: AsyncSession
    conversation: Conversation
    context: OrgContext
    secret_values: Collection[str] | None = None


async def load_live_protocol_events(
    request: LiveProtocolEventsRequest,
) -> list[StoredProtocolEvent]:
    if request.conversation.agent_lifecycle_state == "purged":
        raise ValueError("purged conversations require retained protocol events")
    secrets = request.secret_values
    if secrets is None:
        secrets = tuple(
            await chat_secrets.collect_conversation_secret_values(
                request.db,
                request.conversation,
                request.context,
            )
        )
    records = await trace_storage.get_traces_for_conversation(
        request.db,
        request.conversation.id,
    )
    events: list[StoredProtocolEvent] = []
    for record in records:
        for index, raw in enumerate(
            await trace_storage.load_events(request.db, record),
            start=1,
        ):
            parsed = _JSON_MAPPING.validate_python(raw, strict=True)
            events.extend(
                stored_events_from_raw(RawEventProjectionRequest(parsed, record, index, secrets))
            )
    return resequence_protocol_events(events)


def parse_stored_event_mapping(raw: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return _JSON_MAPPING.validate_python(raw, strict=True)


__all__ = [
    "LiveProtocolEventsRequest",
    "load_live_protocol_events",
    "parse_stored_event_mapping",
]
