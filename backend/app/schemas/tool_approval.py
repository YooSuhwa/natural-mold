"""Tool approval API shapes (approval-modes-plan 3.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.runtime_policy import ToolApprovalToolV1


class ToolApprovalCell(BaseModel):
    path: str
    kind: str  # ask | auto | when | hidden
    layer: str
    reason: str
    delivery: str  # card | wait | per_schedule | per_deployment
    rule: dict[str, Any] | None = None


class ToolApprovalTool(BaseModel):
    key: str  # the resource key settings and grants are saved under
    name: str
    source: str | None = None  # the MCP server it comes from, to tell same-named tools apart
    level: str
    # The company decides this tool whatever the agent says (shown locked).
    locked: bool
    grantable: bool
    cells: list[ToolApprovalCell]


class ToolApprovalView(BaseModel):
    """The agent's settings, the company cap and the effective table ("실제 적용")."""

    # Per-tool entries are keyed by resource key; names that mean one tool are rewritten.
    settings: dict[str, Any] | None
    # Per-tool entries saved by a name that is not one of the agent's tools, or is several.
    unresolved: list[str] = []
    company_actions: dict[str, str]
    company_always_ask: list[str]
    tools: list[ToolApprovalTool]


class ApprovalDefaultUpdate(BaseModel):
    """A tool's (or MCP server's) own approval default; ``null`` clears it."""

    model_config = ConfigDict(extra="forbid")

    default: ToolApprovalToolV1 | None

    def stored(self) -> dict[str, Any] | None:
        return self.default.model_dump(mode="json", exclude_none=True) if self.default else None


def default_change(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """Audit metadata of a default change: the modes, never the rule's values."""
    return {
        "before": (before or {}).get("mode"),
        "after": (after or {}).get("mode"),
    }


class ToolGrantCreate(BaseModel):
    # A resource key, or a tool name when only one of the agent's tools carries it.
    tool: str = Field(min_length=1, max_length=128)


class ToolGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str  # the resource key (a name on grants made before keys)
    created_at: datetime
