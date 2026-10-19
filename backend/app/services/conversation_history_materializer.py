from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeGuard, runtime_checkable

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, DeltaChannelHistory
from langgraph.types import Overwrite
from psycopg import Error as PsycopgError
from sqlalchemy.exc import SQLAlchemyError

from app.models.conversation import Conversation
from app.schemas.conversation_history import JsonValue
from app.services.conversation_history_projection import (
    canonical_payload_sha256,
    sanitize_checkpoint_payload,
)
from app.services.conversation_history_records import (
    build_event_projections,
    build_retained_trace,
)
from app.services.conversation_history_types import (
    CheckpointProjection,
    ConversationHistoryMaterializationRequest,
    EventProjection,
    HistoryProjection,
)
from app.services.conversation_protocol_projection import checkpoint_channel_values
from app.services.thread_branch_service import (
    _normalize_checkpoint_messages,
)


class HistorySourceIncompleteError(RuntimeError):
    """A source row or checkpoint changed during materialization."""


@runtime_checkable
class _DeltaHistorySaver(Protocol):
    async def aget_delta_channel_history(
        self,
        *,
        config: RunnableConfig,
        channels: Sequence[str],
    ) -> Mapping[str, DeltaChannelHistory]: ...


def _supports_delta_history(
    saver: BaseCheckpointSaver[str],
) -> TypeGuard[_DeltaHistorySaver]:
    return isinstance(saver, _DeltaHistorySaver)


async def _load_delta_history(
    saver: BaseCheckpointSaver[str],
    config: RunnableConfig,
) -> Mapping[str, DeltaChannelHistory]:
    if not _supports_delta_history(saver):
        raise HistorySourceIncompleteError("DeltaChannel materializer is unavailable")
    return await saver.aget_delta_channel_history(config=config, channels=["messages"])


@dataclass(frozen=True, slots=True)
class HistoryProjectionRequest:
    request: ConversationHistoryMaterializationRequest
    conversation: Conversation
    secrets: set[str]


async def build_history_projection(source: HistoryProjectionRequest) -> HistoryProjection:
    """Build the canonical sanitized projection without mutating retained tables."""
    trace = await build_retained_trace(
        source.request.db,
        source.conversation.id,
        source.secrets,
    )
    checkpoints = await _checkpoint_projections(source, trace)
    events = await build_event_projections(
        source.request.db,
        source.conversation.id,
        source.secrets,
    )
    if not checkpoints:
        raise HistorySourceIncompleteError("no checkpoint headers were found")
    payload: JsonValue = {
        "schema_version": 1,
        "conversation_id": str(source.conversation.id),
        "source_agent_id": str(source.request.agent_id),
        "checkpoints": [_checkpoint_json(item) for item in checkpoints],
        "events": [_event_json(item) for item in events],
    }
    return HistoryProjection(
        tuple(checkpoints),
        tuple(events),
        canonical_payload_sha256(payload),
    )


async def _checkpoint_projections(
    source: HistoryProjectionRequest,
    trace: dict[str, JsonValue],
) -> list[CheckpointProjection]:
    request = source.request
    conversation = source.conversation
    headers: list[CheckpointProjection] = []
    config: RunnableConfig = {"configurable": {"thread_id": str(conversation.id)}}
    checkpoint_tuples: list[CheckpointTuple] = []
    async for item in request.checkpointer.alist(config):
        checkpoint_tuples.append(item)
    for item in checkpoint_tuples:
        configurable = (item.config or {}).get("configurable", {})
        checkpoint_id = configurable.get("checkpoint_id")
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise HistorySourceIncompleteError("checkpoint header has no identity")
        namespace = configurable.get("checkpoint_ns", "")
        if not isinstance(namespace, str):
            raise HistorySourceIncompleteError("checkpoint namespace is invalid")
        exact: RunnableConfig = {
            "configurable": {
                "thread_id": str(conversation.id),
                "checkpoint_id": checkpoint_id,
                "checkpoint_ns": namespace,
            }
        }
        exact_tuple = await request.checkpointer.aget_tuple(exact)
        if exact_tuple is None:
            raise HistorySourceIncompleteError("checkpoint disappeared during snapshot")
        values = checkpoint_channel_values(exact_tuple)
        checkpoint_values = (item.checkpoint or {}).get("channel_values", {})
        direct_messages = checkpoint_values.get("messages")
        if direct_messages is None:
            try:
                histories = await _load_delta_history(request.checkpointer, exact)
            except (
                AttributeError,
                NotImplementedError,
                PsycopgError,
                RuntimeError,
                SQLAlchemyError,
                TypeError,
                ValueError,
            ) as error:
                raise HistorySourceIncompleteError("DeltaChannel materialization failed") from error
            history = histories.get("messages")
            if history is None:
                raise HistorySourceIncompleteError("DeltaChannel messages are incomplete")
            messages = _normalize_checkpoint_messages(
                history.get("seed"),
                source=f"checkpoint {checkpoint_id} DeltaChannel seed",
            )
            for _, _, batch in history.get("writes", []):
                if isinstance(batch, Overwrite):
                    messages = _normalize_checkpoint_messages(
                        batch.value,
                        source=f"checkpoint {checkpoint_id} DeltaChannel overwrite",
                    )
                else:
                    messages.extend(
                        _normalize_checkpoint_messages(
                            batch,
                            source=f"checkpoint {checkpoint_id} DeltaChannel write",
                        )
                    )
        else:
            messages = _normalize_checkpoint_messages(
                direct_messages,
                source=f"checkpoint {checkpoint_id} messages",
            )
        safe = sanitize_checkpoint_payload(values, messages, source.secrets)
        parent_configurable = (item.parent_config or {}).get("configurable", {})
        parent_id = parent_configurable.get("checkpoint_id")
        if parent_id is not None and not isinstance(parent_id, str):
            raise HistorySourceIncompleteError("checkpoint parent is invalid")
        branch: dict[str, JsonValue] = {
            "active": checkpoint_id == conversation.active_branch_checkpoint_id,
            "parent_checkpoint_id": parent_id,
        }
        payload: JsonValue = {
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": parent_id,
            "checkpoint_namespace": namespace,
            "channel_values": safe.channel_values,
            "messages": safe.messages,
            "branch": branch,
            "trace": trace,
        }
        headers.append(
            CheckpointProjection(
                checkpoint_id,
                parent_id,
                namespace,
                safe.channel_values,
                safe.messages,
                branch,
                trace,
                canonical_payload_sha256(payload),
            )
        )
    return sorted(headers, key=lambda row: (row.checkpoint_namespace, row.checkpoint_id))


def _checkpoint_json(item: CheckpointProjection) -> JsonValue:
    return {
        "checkpoint_id": item.checkpoint_id,
        "parent_checkpoint_id": item.parent_checkpoint_id,
        "checkpoint_namespace": item.checkpoint_namespace,
        "channel_values": item.channel_values,
        "messages": item.messages,
        "branch": item.branch,
        "trace": item.trace,
        "payload_sha256": item.payload_sha256,
    }


def _event_json(item: EventProjection) -> JsonValue:
    return {
        "assistant_message_id": item.assistant_message_id,
        "events": item.events,
        "payload_sha256": item.payload_sha256,
    }


__all__ = [
    "HistoryProjectionRequest",
    "HistorySourceIncompleteError",
    "build_history_projection",
]
