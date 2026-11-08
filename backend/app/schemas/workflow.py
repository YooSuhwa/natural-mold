"""Workflow API shapes (workflow-plan W1). The definition itself is ``workflows.definition``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

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
