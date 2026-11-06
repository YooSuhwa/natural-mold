"""Review of MCP tool definitions (plan 8.2 "새로 발견된 도구", decision D11).

A tool definition is what the model is shown and what decides its risk: description,
input schema, annotations, MCP Apps ``_meta.ui`` and the server it is served from. Its
fingerprint is a hash of those values.

* A tool discovery finds for the first time starts ``pending``: no agent can see or attach
  it until a server manager approves it.
* When an approved tool's live definition changes, the new version waits in
  ``pending_definition``. Agents keep being shown the approved description and schema
  (``approved_definition``) until a manager approves or rejects the new one; a rejected
  version is not flagged again.
* Tools approved before review existed have no fingerprint; the first check adopts the live
  one (they were already in use), and changes after that are reviewed.

Discovery is the only place that adds a tool as pending. Marketplace installs, blueprints
and fixtures add tools the installing person already accepted (approved, adopted at the next
check). Connection tests and the health poll flag changes to existing tools; they never add
rows.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from app.models.mcp_tool import McpTool

ReviewState = Literal["pending", "approved", "rejected"]
REVIEW_STATES: Final[tuple[ReviewState, ...]] = ("pending", "approved", "rejected")
Decision = Literal["approve", "reject"]


@dataclass(frozen=True, slots=True)
class Definition:
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]
    metadata: dict[str, Any]  # normalized MCP Apps ``_meta.ui`` (app.mcp.apps)

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, Any]) -> Definition:
        return cls(
            description=str(descriptor.get("description") or ""),
            input_schema=dict(descriptor.get("input_schema") or {}),
            annotations=dict(descriptor.get("annotations") or {}),
            metadata=dict(descriptor.get("metadata") or {}),
        )

    @classmethod
    def from_row(cls, tool: McpTool) -> Definition:
        return cls(
            description=tool.description or "",
            input_schema=dict(tool.input_schema or {}),
            annotations=dict(tool.annotations_json or {}),
            metadata=dict(tool.metadata_json or {}),
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "input_schema": self.input_schema,
            "annotations": self.annotations,
            "metadata": self.metadata,
        }


def fingerprint(name: str, definition: Definition, server_url: str | None) -> str:
    canonical = json.dumps(
        {"name": name, "destination": server_url or "", **definition.as_json()},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _apply(tool: McpTool, definition: Definition) -> None:
    tool.description = definition.description or None
    tool.input_schema = definition.input_schema
    tool.annotations_json = definition.annotations
    tool.metadata_json = definition.metadata


def new_pending_tool(
    server_id: uuid.UUID, name: str, definition: Definition, server_url: str | None
) -> McpTool:
    """A tool discovery found for the first time: hidden until a manager approves it."""
    tool = McpTool(
        server_id=server_id,
        name=name,
        enabled=True,
        review_state="pending",
        last_seen_at=_now(),
    )
    _apply(tool, definition)
    tool.pending_fingerprint = fingerprint(name, definition, server_url)
    return tool


@dataclass(frozen=True, slots=True)
class Observed:
    """What one check changed: tools whose new version now waits for review."""

    flagged: tuple[McpTool, ...]


def observe(
    tools: Sequence[McpTool], descriptors: Sequence[Mapping[str, Any]], server_url: str | None
) -> Observed:
    """Compare live definitions with the stored ones (no new rows; see ``new_pending_tool``)."""
    by_name = {tool.name: tool for tool in tools}
    flagged: list[McpTool] = []
    stamp = _now()
    for descriptor in descriptors:
        tool = by_name.get(str(descriptor.get("name") or ""))
        if tool is None:
            continue
        tool.last_seen_at = stamp
        definition = Definition.from_descriptor(descriptor)
        live = fingerprint(tool.name, definition, server_url)
        if tool.review_state != "approved":
            # Never approved: the version a manager will see is the latest one.
            if live != tool.pending_fingerprint and live != tool.rejected_fingerprint:
                _apply(tool, definition)
                tool.pending_fingerprint = live
                if tool.review_state == "rejected":
                    tool.review_state = "pending"
                flagged.append(tool)
            continue
        if tool.approved_fingerprint is None:
            # Approved before reviews existed: adopt what it serves now.
            _apply(tool, definition)
            tool.approved_fingerprint = live
            continue
        if live in (tool.approved_fingerprint, tool.rejected_fingerprint):
            if live == tool.approved_fingerprint and tool.pending_definition is not None:
                tool.pending_definition = None  # the server went back to the approved one
                tool.pending_fingerprint = None
            continue
        if live != tool.pending_fingerprint:
            tool.pending_definition = definition.as_json()
            tool.pending_fingerprint = live
            flagged.append(tool)
    return Observed(tuple(flagged))


class ReviewConflict(Exception):
    """Nothing waits for review on this tool (or it changed since the manager looked)."""


def decide(
    tool: McpTool,
    decision: Decision,
    *,
    reviewer_id: uuid.UUID,
    server_url: str | None,
    expected_fingerprint: str | None,
) -> str:
    """Apply a manager's decision; returns the fingerprint it applied to.

    ``expected_fingerprint`` is the version the manager reviewed; a newer one arriving in
    between is a conflict rather than an approval of something unseen.
    """
    if tool.review_state == "approved":
        if tool.pending_definition is None or tool.pending_fingerprint is None:
            raise ReviewConflict("no change waits for review")
        target = tool.pending_fingerprint
    elif tool.review_state in ("pending", "rejected"):
        target = tool.pending_fingerprint or fingerprint(
            tool.name, Definition.from_row(tool), server_url
        )
    else:
        raise ReviewConflict(f"unknown review state {tool.review_state!r}")
    if expected_fingerprint is not None and expected_fingerprint != target:
        raise ReviewConflict("the definition changed since it was reviewed")

    if decision == "approve":
        if tool.review_state == "approved" and tool.pending_definition is not None:
            _apply(tool, _definition_from_json(tool.pending_definition))
        tool.review_state = "approved"
        tool.approved_fingerprint = target
        tool.rejected_fingerprint = None
    else:
        tool.rejected_fingerprint = target
        if tool.review_state != "approved":
            tool.review_state = "rejected"
    tool.pending_definition = None
    tool.pending_fingerprint = None
    tool.reviewed_at = _now()
    tool.reviewed_by = reviewer_id
    return target


def _definition_from_json(data: Mapping[str, Any]) -> Definition:
    return Definition(
        description=str(data.get("description") or ""),
        input_schema=dict(data.get("input_schema") or {}),
        annotations=dict(data.get("annotations") or {}),
        metadata=dict(data.get("metadata") or {}),
    )


def is_usable(tool: McpTool) -> bool:
    """Agents can see and call it (an approved version exists and it is switched on)."""
    return bool(tool.enabled) and tool.review_state == "approved"


def approved_definition(tool: McpTool) -> dict[str, Any] | None:
    """What the model is shown for a reviewed tool (``None``: use what the server lists)."""
    if tool.review_state != "approved" or tool.approved_fingerprint is None:
        return None
    definition = Definition.from_row(tool)
    return {
        "description": definition.description,
        "input_schema": definition.input_schema,
        "annotations": definition.annotations,
    }


def pending_view(tool: McpTool, server_url: str | None) -> dict[str, Any] | None:
    """The version waiting for review, for the manager's screen."""
    if tool.review_state == "approved":
        if tool.pending_definition is None:
            return None
        return {**dict(tool.pending_definition), "fingerprint": tool.pending_fingerprint}
    if tool.review_state == "pending":
        return {
            **Definition.from_row(tool).as_json(),
            "fingerprint": tool.pending_fingerprint
            or fingerprint(tool.name, Definition.from_row(tool), server_url),
        }
    return None


__all__ = [
    "REVIEW_STATES",
    "Decision",
    "Definition",
    "Observed",
    "ReviewConflict",
    "ReviewState",
    "approved_definition",
    "decide",
    "fingerprint",
    "is_usable",
    "new_pending_tool",
    "observe",
    "pending_view",
]
