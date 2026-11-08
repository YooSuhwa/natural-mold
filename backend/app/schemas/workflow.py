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
