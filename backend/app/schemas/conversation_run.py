from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, PlainSerializer


def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str, when_used="json")]


class ConversationRunActivityResponse(BaseModel):
    kind: str
    namespace: list[str]
    call_id: str | None = None
    name: str | None = None
    elapsed_ms: float | None = None


class ConversationRunMetricsResponse(BaseModel):
    terminal_state: str | None = None
    elapsed_ms: float | None = None
    ttft_ms: float | None = None
    generation_ms: float | None = None
    tokens_per_second: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    estimated_cost: float | None = None
    usage_complete: bool
    root_tool_calls: int | None = None
    descendant_tool_calls: int | None = None
    root_subagent_calls: int | None = None
    descendant_subagent_calls: int | None = None
    activity_json: list[ConversationRunActivityResponse]
    activity_truncated: bool

    model_config = {"from_attributes": True}


class ConversationRunResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    agent_id: uuid.UUID
    parent_run_id: uuid.UUID | None = None
    status: str
    source: str
    worker_instance_id: str | None = None
    interrupt_id: str | None = None
    last_event_id: str | None = None
    input_preview: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    runtime_policy_version: int | None = None
    runtime_policy_hash: str | None = None
    runtime_policy_source: str | None = None
    cancel_requested_at: UtcDatetime | None = None
    cancel_reason: str | None = None
    cancellation_acknowledged_at: UtcDatetime | None = None
    started_at: UtcDatetime | None = None
    heartbeat_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    metrics: ConversationRunMetricsResponse | None = None

    model_config = {"from_attributes": True}
