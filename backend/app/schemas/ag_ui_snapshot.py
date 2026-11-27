"""Conversation state used to initialize or recover a public AG-UI chat."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from ag_ui.core import ActivityMessage, AssistantMessage, Interrupt, ToolMessage, UserMessage
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.services.partial_model_output import PartialOutput


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class AgUiSnapshotMessageLink(_SnapshotModel):
    message_id: str
    ui_message_id: str | None = None
    run_id: str | None = None
    physical_run_id: str | None = None
    checkpoint_id: str | None = None
    parent_checkpoint_id: str | None = None
    sibling_checkpoint_ids: list[str] = Field(default_factory=list)
    parent_message_id: str | None = None
    subagent_run_id: str | None = None
    parent_tool_call_id: str | None = None
    created_at: datetime | None = None


class SnapshotTodo(_SnapshotModel):
    id: str
    content: str
    status: Literal["pending", "in_progress", "completed"]


class SnapshotSubagent(_SnapshotModel):
    subagent_run_id: str
    parent_tool_call_id: str | None = None
    parent_message_id: str | None = None
    parent_subagent_run_id: str | None = None
    parent_run_id: str | None = None
    run_id: str | None = None
    physical_run_id: str | None = None
    child_task_id: str | None = None
    name: str
    description: str | None = None
    status: Literal["running", "completed", "failed", "interrupted", "cancelled"]
    result: str | None = None
    error: str | None = None
    interrupt_ids: list[str] = Field(default_factory=list)
    legacy_interrupt_access: bool = False
    todos: list[SnapshotTodo] = Field(default_factory=list)


type PublicMessage = Annotated[
    UserMessage | AssistantMessage | ToolMessage | ActivityMessage, Field(discriminator="role")
]


class AgUiSnapshot(_SnapshotModel):
    thread_id: str
    protocol_version: Literal["1.0"] = "1.0"
    projection_version: Literal["1"] = "1"
    checkpoint_id: str | None = None
    active_tip_message_id: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    latest_physical_run_id: str | None = None
    active_run_id: str | None = None
    physical_run_id: str | None = None
    interrupt_run_id: str | None = None
    physical_interrupt_run_id: str | None = None
    input_id: str | None = None
    input_status: str | None = None
    input_revision: int | None = None
    queue_status: str | None = None
    position: int | None = None
    failed_physical_run_id: str | None = None
    error_code: str | None = None
    partial_output: PartialOutput | None = None
    messages: list[PublicMessage] = Field(default_factory=list)
    message_links: list[AgUiSnapshotMessageLink] = Field(default_factory=list)
    interrupts: list[Interrupt] = Field(default_factory=list)
    subagents: list[SnapshotSubagent] = Field(default_factory=list)
    todos: list[SnapshotTodo] = Field(default_factory=list)
    cursor: str | None = None
