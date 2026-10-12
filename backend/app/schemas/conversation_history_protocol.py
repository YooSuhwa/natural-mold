from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.conversation_refs import ConversationRefSource, JsonValue, conversation_ref


class ThreadCheckpoint(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    thread_id: str | None = None
    checkpoint_id: str | None = None
    checkpoint_ns: str | None = None


class ThreadHistoryCursor(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    checkpoint_id: str | None = None
    checkpoint_ns: str | None = None
    configurable: ThreadCheckpoint | None = None


class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=10, ge=1, le=100)
    before: ThreadHistoryCursor | None = None
    metadata: dict[str, JsonValue] | None = None
    checkpoint: ThreadCheckpoint | None = None


class ThreadCheckpointPayload(TypedDict):
    thread_id: str
    checkpoint_id: str
    checkpoint_ns: str


class RunMetadata(TypedDict):
    id: str
    status: str
    error_code: NotRequired[str]
    error_message: NotRequired[str]


class ThreadStateMetadata(TypedDict):
    conversation_id: str
    agent_id: str | None
    source: str
    checkpoint_by_message_id: dict[str, str]
    parent_checkpoint_by_message_id: dict[str, str]
    active_run: NotRequired[RunMetadata]
    latest_run: NotRequired[RunMetadata | None]


class ThreadStateResponse(TypedDict):
    values: dict[str, JsonValue]
    next: list[str]
    tasks: Sequence[Mapping[str, JsonValue]]
    checkpoint: ThreadCheckpointPayload | None
    metadata: ThreadStateMetadata
    created_at: str
    parent_checkpoint: Mapping[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class ThreadStateResponseInput:
    values: Mapping[str, JsonValue] | None = None
    next_nodes: list[str] | None = None
    tasks: Sequence[Mapping[str, JsonValue]] | None = None
    checkpoint_id: str | None = None
    checkpoint_ns: str = ""
    checkpoint_by_message_id: Mapping[str, str] | None = None
    parent_checkpoint_by_message_id: Mapping[str, str] | None = None
    active_run: RunMetadata | None = None
    latest_run: RunMetadata | None = None
    metadata_source: str = "moldy_bff_fallback"
    created_at: str | None = None
    parent_checkpoint: Mapping[str, JsonValue] | None = None


def _run_metadata_json(metadata: RunMetadata) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {"id": metadata["id"], "status": metadata["status"]}
    if "error_code" in metadata:
        value["error_code"] = metadata["error_code"]
    if "error_message" in metadata:
        value["error_message"] = metadata["error_message"]
    return value


def build_state_response(
    conversation: ConversationRefSource,
    request: ThreadStateResponseInput,
) -> ThreadStateResponse:
    conversation = conversation_ref(conversation)
    state_values = dict(request.values or {})
    state_values.setdefault("messages", [])
    runtime_state: dict[str, JsonValue] = {}
    if request.active_run is not None:
        runtime_state["active_run"] = _run_metadata_json(request.active_run)
    if request.latest_run is not None:
        runtime_state["latest_run"] = _run_metadata_json(request.latest_run)
    if runtime_state:
        state_values["__moldy_runtime"] = runtime_state
    resolved_checkpoint_id = request.checkpoint_id or conversation.active_branch_checkpoint_id
    checkpoint = (
        ThreadCheckpointPayload(
            thread_id=str(conversation.id),
            checkpoint_id=resolved_checkpoint_id,
            checkpoint_ns=request.checkpoint_ns,
        )
        if resolved_checkpoint_id
        else None
    )
    metadata = ThreadStateMetadata(
        conversation_id=str(conversation.id),
        agent_id=str(conversation.agent_id),
        source=request.metadata_source,
        checkpoint_by_message_id=dict(request.checkpoint_by_message_id or {}),
        parent_checkpoint_by_message_id=dict(request.parent_checkpoint_by_message_id or {}),
    )
    if request.active_run is not None:
        metadata["active_run"] = request.active_run.copy()
    if request.latest_run is not None:
        metadata["latest_run"] = request.latest_run.copy()
    return ThreadStateResponse(
        values=state_values,
        next=request.next_nodes or [],
        tasks=request.tasks or [],
        checkpoint=checkpoint,
        metadata=metadata,
        created_at=request.created_at or conversation.updated_at.isoformat(),
        parent_checkpoint=request.parent_checkpoint,
    )


__all__ = [
    "HistoryRequest",
    "RunMetadata",
    "ThreadCheckpoint",
    "ThreadHistoryCursor",
    "ThreadStateResponse",
    "ThreadStateResponseInput",
    "build_state_response",
]
