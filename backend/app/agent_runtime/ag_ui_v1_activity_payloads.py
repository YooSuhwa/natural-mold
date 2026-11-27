"""Allowlisted DTOs for the ordinary chat activity stream."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.agent_runtime.protocol_redaction import REDACTED_MEMORY_FIELD


def nonempty(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        canonical = str(UUID(value))
        return canonical if canonical == value else None
    except ValueError:
        return None


def public_file(
    payload: Mapping[str, Any],
    event: StoredProtocolEvent,
    readable_versions: frozenset[tuple[str, str]],
) -> dict[str, Any] | None:
    # The row is a notification. Current file metadata comes from the artifact
    # service after its independent conversation and version ownership check.
    if (
        payload.get("conversation_id") != event["thread_id"]
        or payload.get("run_id") != event["run_id"]
    ):
        return None
    artifact, version = canonical_uuid(payload.get("id")), canonical_uuid(payload.get("version_id"))
    op = payload.get("op")
    if (
        not artifact
        or not version
        or (artifact, version) not in readable_versions
        or op not in ("created", "updated", "deleted", "failed")
    ):
        return None
    return {"id": artifact, "version_id": version, "op": op}


def public_agent_memo(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    proposal_id = canonical_uuid(payload.get("id"))
    if payload.get("scope") != "agent_memo" or not proposal_id:
        return None
    return {"scope": "agent_memo", "id": proposal_id, "content": REDACTED_MEMORY_FIELD}


def public_compaction(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if payload.get("state") not in ("running", "done"):
        return None
    result = {"state": payload["state"]}
    if result["state"] == "done":
        history = payload.get("history_id")
        if isinstance(history, str) and re.fullmatch(r"history_[0-9a-f]{24}", history):
            result["history_id"] = history
        cutoff = payload.get("cutoff_index")
        if type(cutoff) is int and cutoff > 0:
            result["cutoff_index"] = cutoff
    return result


def public_usage(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    value: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_creation_tokens"):
        count = payload.get(key)
        if type(count) is int and 0 <= count <= 2**53 - 1:
            value[key] = count
    if "prompt_tokens" not in value or "completion_tokens" not in value:
        return None
    for key in ("estimated_cost", "ttft_ms", "generation_ms", "tokens_per_second"):
        metric = payload.get(key)
        if (
            isinstance(metric, (int, float))
            and not isinstance(metric, bool)
            and 0 <= metric <= 2**53 - 1
            and math.isfinite(metric)
        ):
            value[key] = metric
    if mid := nonempty(payload.get("assistant_msg_id")):
        value["assistant_msg_id"] = mid
    return value


def public_memory(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    scope = payload.get("scope")
    if scope not in ("user", "agent"):
        return None
    # Rendering reads the contents under the viewer's current memory grants.
    out = {"scope": scope, "content": REDACTED_MEMORY_FIELD}
    for key in ("id", "line_id", "proposal_id", "memory_id"):
        if ref := canonical_uuid(payload.get(key)):
            out[key] = ref
    if payload.get("path") in ("profile", "auto", "tool"):
        out["path"] = payload["path"]
    return out


def public_recall(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = payload.get("memories")
    if not isinstance(rows, list):
        return None
    memories = [brief for row in rows if isinstance(row, Mapping) and (brief := public_memory(row))]
    return {"memories": memories} if memories else None


def public_exclusions(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    groups = payload.get("items")
    if not isinstance(groups, list):
        return None
    out = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        kind, reason, count = group.get("type"), group.get("reason"), group.get("count")
        if (
            kind in ("tool", "skill", "mcp_tool", "agent")
            and reason in ("broken", "denied")
            and type(count) is int
            and 0 < count <= 2**53 - 1
        ):
            out.append({"type": kind, "reason": reason, "count": count})
    return {"items": out, "total": sum(row["count"] for row in out)} if out else None


def public_todos(state: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = state.get("todos")
    if not isinstance(rows, list):
        return None
    todos = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or not nonempty(row.get("content")):
            return None
        status = row.get("status")
        if status not in ("pending", "in_progress", "completed"):
            return None
        todos.append(
            {
                "id": nonempty(row.get("id")) or f"todo-{index + 1}",
                "content": row["content"],
                "status": status,
            }
        )
    return {"todos": todos}


def widget_pointer(event: StoredProtocolEvent) -> dict[str, Any] | None:
    """Return an inert reference; the widget's authorized GET supplies view data."""
    data = event["data"]
    if not isinstance(data, Mapping):
        return None
    if event["method"] == "messages" and data.get("type") not in ("tool", "ToolMessage"):
        return None
    if event["method"] == "tools" and data.get("event") != "tool-finished":
        return None
    artifact = data.get("artifact")
    app = artifact.get("mcp_app") if isinstance(artifact, Mapping) else None
    if not isinstance(app, Mapping) or type(app.get("version")) is not int or app["version"] != 1:
        return None
    binding = canonical_uuid(app.get("binding_id"))
    run = canonical_uuid(app.get("run_id"))
    call = nonempty(data.get("tool_call_id"))
    if not binding or not call or not run or run != event["run_id"]:
        return None
    return {"binding_id": binding, "run_id": run, "tool_call_id": call}
