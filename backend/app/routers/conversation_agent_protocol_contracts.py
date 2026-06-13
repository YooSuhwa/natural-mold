from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.protocol_events import SubscribeParams
from app.models.conversation import Conversation

LANGGRAPH_PROTOCOL_HEADER = "langgraph_v3"
SUPPORTED_COMMAND_METHODS = {"run.start", "input.respond"}


class ThreadCheckpoint(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    thread_id: str | None = None
    checkpoint_id: str | None = None
    checkpoint_ns: str | None = None


class InputRespondEntry(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    namespace: list[str] = Field(default_factory=list)
    interrupt_id: str
    response: Any


class AgentCommandParams(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    assistant_id: str | None = None
    input: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    checkpoint: ThreadCheckpoint | None = None
    multitask_strategy: str | None = None
    namespace: list[str] | None = None
    interrupt_id: str | None = None
    response: Any = None
    responses: list[InputRespondEntry] | None = Field(default=None, min_length=1)


class AgentCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int | str | None = None
    method: str
    params: AgentCommandParams = Field(default_factory=AgentCommandParams)


class SubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channels: list[str] | None = None
    namespaces: list[list[str]] | None = None
    depth: int | None = None
    since: int | str | None = None

    def as_params(self) -> SubscribeParams:
        params: SubscribeParams = {}
        if self.channels is not None:
            params["channels"] = list(self.channels)
        if self.namespaces is not None:
            params["namespaces"] = [list(namespace) for namespace in self.namespaces]
        if self.depth is not None:
            params["depth"] = self.depth
        if self.since is not None:
            params["since"] = self.since
        return params


class UpdateStateRequest(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    values: dict[str, Any] = Field(default_factory=dict)
    checkpoint: ThreadCheckpoint | None = None
    as_node: str | None = None
    task_id: str | None = None


class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=10, ge=1, le=100)
    before: ThreadCheckpoint | None = None


def protocol_headers(*, mode: str | None = None, run_id: str | None = None) -> dict[str, str]:
    headers = {"X-Stream-Protocol": LANGGRAPH_PROTOCOL_HEADER}
    if mode is not None:
        headers["X-Resume-Mode"] = mode
    if run_id is not None:
        headers["X-Run-Id"] = run_id
    return headers


def state_response(
    conversation: Conversation,
    *,
    values: dict[str, Any] | None = None,
    next_nodes: list[str] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    checkpoint_id: str | None = None,
    checkpoint_ns: str = "",
    checkpoint_by_message_id: dict[str, str] | None = None,
    metadata_source: str = "moldy_bff_fallback",
    created_at: str | None = None,
    parent_checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_values = dict(values or {})
    state_values.setdefault("messages", [])
    resolved_checkpoint_id = checkpoint_id or conversation.active_branch_checkpoint_id
    checkpoint = (
        {
            "thread_id": str(conversation.id),
            "checkpoint_id": resolved_checkpoint_id,
            "checkpoint_ns": checkpoint_ns,
        }
        if resolved_checkpoint_id
        else None
    )
    metadata: dict[str, Any] = {
        "conversation_id": str(conversation.id),
        "agent_id": str(conversation.agent_id),
        "source": metadata_source,
        "checkpoint_by_message_id": dict(checkpoint_by_message_id or {}),
    }
    return {
        "values": state_values,
        "next": next_nodes or [],
        "tasks": tasks or [],
        "checkpoint": checkpoint,
        "metadata": metadata,
        "created_at": created_at or conversation.updated_at.isoformat(),
        "parent_checkpoint": parent_checkpoint,
    }


def command_error(command: AgentCommandRequest, *, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {
            "type": "error",
            "id": command.id,
            "error": {"code": code, "message": message},
        },
        headers=protocol_headers(),
    )


def command_success(
    command: AgentCommandRequest,
    *,
    conversation: Conversation,
    thread_id: str,
    run_id: str | None = None,
) -> JSONResponse:
    strategy = command.params.multitask_strategy or "reject"
    result = {
        "status": "accepted",
        "conversation_id": str(conversation.id),
        "thread_id": thread_id,
        "multitask_strategy": strategy,
    }
    if run_id is not None:
        result["run_id"] = run_id
    return JSONResponse(
        {
            "type": "success",
            "id": command.id,
            "result": result,
            "meta": {"thread_id": thread_id},
        },
        headers=protocol_headers(run_id=run_id),
    )
