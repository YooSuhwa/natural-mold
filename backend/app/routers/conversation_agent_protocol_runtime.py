from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    SubscribeParams,
    format_protocol_sse,
    matches_subscription,
)
from app.error_codes import conversation_not_found
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.routers.conversation_agent_protocol_contracts import AgentCommandRequest
from app.routers.conversation_agent_protocol_event_normalization import (
    stored_compatible_protocol_event,
)
from app.routers.conversation_agent_protocol_legacy import (
    protocol_events_from_legacy_sse,
)
from app.services import chat_service

SUPPORTED_MULTITASK_STRATEGIES = {"reject"}


async def get_owned_thread(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    thread_id: str,
    user_id: uuid.UUID,
) -> Conversation:
    if thread_id != str(conversation_id):
        raise conversation_not_found()

    conversation = await chat_service.get_owned_conversation(db, conversation_id, user_id)
    if conversation is None:
        raise conversation_not_found()
    return conversation


def cfg_agent_uuid(conversation: Conversation) -> uuid.UUID:
    return conversation.agent_id


def command_multitask_strategy(command: AgentCommandRequest) -> str:
    return command.params.multitask_strategy or "reject"


def string_content(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Mapping):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts) if parts else None


def input_preview(input_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(input_payload, Mapping):
        return None
    messages = input_payload.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        role = message.get("role") or message.get("type")
        if role not in {"user", "human"}:
            continue
        return string_content(message.get("content"))
    return None


def checkpoint_id(command: AgentCommandRequest) -> str | None:
    checkpoint = command.params.checkpoint
    if checkpoint is not None and checkpoint.checkpoint_id:
        return checkpoint.checkpoint_id

    config = command.params.config
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    value = configurable.get("checkpoint_id")
    return value if isinstance(value, str) and value else None


def _int_value(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _namespace(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [segment for segment in value if isinstance(segment, str)]


def protocol_events_from_broker(
    event: BrokeredEvent,
    *,
    run_id: str,
    thread_id: str,
) -> list[StoredProtocolEvent]:
    payload = event.get("data")
    if not isinstance(payload, Mapping):
        return []
    method = _optional_str(payload.get("method"))
    seq = _int_value(payload.get("seq"))
    params = payload.get("params")
    if method is None or seq is None or not isinstance(params, Mapping):
        return protocol_events_from_legacy_sse(
            event,
            record=_broker_record(run_id=run_id, thread_id=thread_id),
            seq=0,
        )
    return [
        stored_compatible_protocol_event(
            run_id=run_id,
            thread_id=thread_id,
            seq=seq,
            method=method,
            namespace=_namespace(params.get("namespace")),
            data=params.get("data"),
            event_id=_optional_str(payload.get("event_id")),
            timestamp=_optional_str(params.get("timestamp")),
            checkpoint_id=_optional_str(params.get("checkpoint_id")),
            checkpoint_ns=_optional_str(params.get("checkpoint_ns")),
        )
    ]


def _broker_record(*, run_id: str, thread_id: str) -> MessageEvent:
    return MessageEvent(
        conversation_id=uuid.UUID(thread_id),
        assistant_msg_id=run_id,
        events=[],
    )


async def protocol_broker_generator(
    broker: EventBroker,
    *,
    thread_id: str,
    params: SubscribeParams,
    after_id: str | None,
) -> AsyncGenerator[str, None]:
    async for event in broker.subscribe(after_id=after_id):
        for protocol_event in protocol_events_from_broker(
            event,
            run_id=broker.run_id,
            thread_id=thread_id,
        ):
            if matches_subscription(protocol_event, params):
                yield format_protocol_sse(protocol_event)
