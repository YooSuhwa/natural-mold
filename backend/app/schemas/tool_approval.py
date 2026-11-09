"""Tool approval API shapes (approval-modes-plan 3.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolApprovalCell(BaseModel):
    path: str
    kind: str  # ask | auto | when | hidden
    layer: str
    reason: str
    delivery: str  # card | wait | per_schedule | per_deployment
    rule: dict[str, Any] | None = None


class ToolApprovalTool(BaseModel):
    name: str
    level: str
    # The company decides this tool whatever the agent says (shown locked).
    locked: bool
    grantable: bool
    cells: list[ToolApprovalCell]


class ToolApprovalView(BaseModel):
    """The agent's settings, the company cap and the effective table ("실제 적용")."""

    settings: dict[str, Any] | None
    company_actions: dict[str, str]
    company_always_ask: list[str]
    tools: list[ToolApprovalTool]


class ToolGrantCreate(BaseModel):
    tool: str = Field(min_length=1, max_length=128)


class ToolGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    created_at: datetime
