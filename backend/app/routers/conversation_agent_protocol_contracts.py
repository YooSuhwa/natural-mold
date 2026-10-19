from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NotRequired, TypedDict

from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.agent_runtime.protocol_events import SubscribeParams
from app.schemas.conversation_history_protocol import (
    HistoryRequest as HistoryRequest,
)
from app.schemas.conversation_history_protocol import (
    RunMetadata,
    ThreadCheckpoint,
    ThreadStateResponse,
    ThreadStateResponseInput,
    build_state_response,
)
from app.schemas.conversation_history_protocol import (
    ThreadHistoryCursor as ThreadHistoryCursor,
)
from app.schemas.conversation_refs import ConversationRefSource, JsonValue, conversation_ref

LANGGRAPH_PROTOCOL_HEADER = "langgraph_v3"
SUPPORTED_COMMAND_METHODS = {"run.start", "input.respond"}


class CommandSuccessResult(TypedDict):
    status: str
    conversation_id: str
    thread_id: str
    multitask_strategy: str
    run_id: NotRequired[str]
    input_id: NotRequired[str]
    input_status: NotRequired[str | None]
    revision: NotRequired[int | None]
    position: NotRequired[int | None]


class InputRespondEntry(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    namespace: list[str] = Field(default_factory=list)
    interrupt_id: str
    response: Any


class AgentCommandParams(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    assistant_id: str | None = None
    input: dict[str, JsonValue] | None = None
    config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    checkpoint: ThreadCheckpoint | None = None
    multitask_strategy: str | None = Field(
        default=None,
        validation_alias=AliasChoices("multitask_strategy", "multitaskStrategy"),
    )
    client_request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("client_request_id", "clientRequestId"),
    )
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


def protocol_headers(*, mode: str | None = None, run_id: str | None = None) -> dict[str, str]:
    headers = {"X-Stream-Protocol": LANGGRAPH_PROTOCOL_HEADER}
    if mode is not None:
        headers["X-Resume-Mode"] = mode
    if run_id is not None:
        headers["X-Run-Id"] = run_id
    return headers


def state_response(
    conversation: ConversationRefSource,
    *,
    values: dict[str, Any] | None = None,
    next_nodes: list[str] | None = None,
    tasks: Sequence[Mapping[str, Any]] | None = None,
    checkpoint_id: str | None = None,
    checkpoint_ns: str = "",
    checkpoint_by_message_id: dict[str, str] | None = None,
    parent_checkpoint_by_message_id: dict[str, str] | None = None,
    active_run: Mapping[str, Any] | None = None,
    latest_run: Mapping[str, Any] | None = None,
    metadata_source: str = "moldy_bff_fallback",
    created_at: str | None = None,
    parent_checkpoint: dict[str, Any] | None = None,
) -> ThreadStateResponse:
    return build_state_response(
        conversation,
        ThreadStateResponseInput(
            values=values,
            next_nodes=next_nodes,
            tasks=tasks,
            checkpoint_id=checkpoint_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_by_message_id=checkpoint_by_message_id,
            parent_checkpoint_by_message_id=parent_checkpoint_by_message_id,
            active_run=RunMetadata(**active_run) if active_run is not None else None,
            latest_run=RunMetadata(**latest_run) if latest_run is not None else None,
            metadata_source=metadata_source,
            created_at=created_at,
            parent_checkpoint=parent_checkpoint,
        ),
    )


def command_error(
    command: AgentCommandRequest,
    *,
    code: str,
    message: str,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        {
            "type": "error",
            "id": command.id,
            "error": {"code": code, "message": message},
        },
        status_code=status_code,
        headers=protocol_headers(),
    )


def command_success(
    command: AgentCommandRequest,
    *,
    conversation: ConversationRefSource,
    thread_id: str,
    run_id: str | None = None,
    input_id: str | None = None,
    input_status: str | None = None,
    revision: int | None = None,
    position: int | None = None,
) -> JSONResponse:
    conversation = conversation_ref(conversation)
    strategy = command.params.multitask_strategy or "reject"
    result: CommandSuccessResult = {
        "status": "accepted",
        "conversation_id": str(conversation.id),
        "thread_id": thread_id,
        "multitask_strategy": strategy,
    }
    if run_id is not None:
        result["run_id"] = run_id
    if input_id is not None:
        result["input_id"] = input_id
        result["input_status"] = input_status
        result["revision"] = revision
        result["position"] = position
    return JSONResponse(
        {
            "type": "success",
            "id": command.id,
            "result": result,
            "meta": {"thread_id": thread_id},
        },
        headers=protocol_headers(run_id=run_id),
    )
