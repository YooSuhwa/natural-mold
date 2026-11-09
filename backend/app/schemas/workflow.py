"""Workflow API shapes (workflow-plan W1). The definition itself is ``workflows.definition``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class WorkflowDraftUpdate(BaseModel):
    """``revision`` is the draft revision the edit started from (409 when stale)."""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    definition: dict[str, Any] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class WorkflowPublish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    draft_revision: int
    published_version_no: int | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkflowDetail(WorkflowSummary):
    draft_definition: dict[str, Any]


class WorkflowIssue(BaseModel):
    code: str
    message: str
    node_id: str | None = None


class WorkflowIssues(BaseModel):
    draft_revision: int
    issues: list[WorkflowIssue]


class WorkflowVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_no: int
    definition_hash: str
    published_by: uuid.UUID | None
    published_at: datetime


class WorkflowVersionDetail(WorkflowVersionSummary):
    definition: dict[str, Any]
    agent_pins: dict[str, Any]


class WorkflowRunStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int | None = None
    status: str
    error_code: str | None
    error_message: str | None
    current_node: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class WorkflowStepRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    node_id: str
    node_type: str
    # Loop round (workflow-loop-plan 3.3): 1, then +1 each time a loop runs the node again.
    round: int = 1
    status: str
    branch: str | None
    output: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    conversation_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None


class WorkflowRunDetail(WorkflowRunSummary):
    input: dict[str, Any]
    output: dict[str, Any] | None
    steps: list[WorkflowStepRunOut]


class WorkflowAgentUsage(BaseModel):
    id: uuid.UUID
    name: str
    published_version_no: int | None
    in_draft: bool
    in_published: bool
    drifted: bool


class WorkflowAssistTurn(BaseModel):
    """An earlier turn of the AI editing conversation on this screen (W5)."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=4000)


class WorkflowAssistRequest(BaseModel):
    """``revision`` is the draft the person sees; the proposal is a change of it (409 when
    stale)."""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=4000)
    history: list[WorkflowAssistTurn] = Field(default_factory=list, max_length=20)


class WorkflowAssistResponse(BaseModel):
    """``question``: the model asks back. ``proposal``: ``definition`` is the draft after the
    edit (not saved) with its publish-check ``issues``. ``failed``: no usable edit."""

    kind: Literal["question", "proposal", "failed"]
    message: str
    plan: list[str] = []
    definition: dict[str, Any] | None = None
    issues: list[WorkflowIssue] = []
    base_revision: int


class AgentWorkflowToolsUpdate(BaseModel):
    """The workflows an agent may call as chat tools, in order (workflow-tool-plan 3.1)."""

    model_config = ConfigDict(extra="forbid")

    workflow_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class AgentWorkflowTool(BaseModel):
    workflow_id: uuid.UUID
    name: str
    description: str | None = None
    published_version_no: int | None = None
    usable: bool
    broken_reason: str | None = None
