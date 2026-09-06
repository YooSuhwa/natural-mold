"""Verify MCP App message candidates against runtime-minted DB bindings."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.schemas.mcp_apps import McpAppArtifact
from app.services.mcp_apps_projection_service import load_verified_artifact

_MAX_SCAN_DEPTH = 8
_MAX_CANDIDATES = 32


@dataclass(frozen=True, slots=True)
class McpAppCandidate:
    tool_call_id: str
    artifact: McpAppArtifact


def extract_mcp_app_candidates(value: Any) -> list[McpAppCandidate]:
    """Extract untrusted candidates without granting them projection authority."""

    found: list[McpAppCandidate] = []

    def visit(item: Any, depth: int) -> None:
        if depth > _MAX_SCAN_DEPTH or len(found) >= _MAX_CANDIDATES:
            return
        if isinstance(item, Mapping):
            _append_candidate(found, item.get("tool_call_id"), item.get("artifact"))
            for child in list(item.values())[:128]:
                visit(child, depth + 1)
            return
        if isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            for child in item[:128]:
                visit(child, depth + 1)
            return
        _append_candidate(
            found,
            getattr(item, "tool_call_id", None),
            getattr(item, "artifact", None),
        )

    visit(value, 0)
    return list({(item.tool_call_id, item.artifact.binding_id): item for item in found}.values())


async def attach_verified_mcp_apps(
    event: StoredProtocolEvent,
    raw_value: Any,
    *,
    expected_conversation_id: str,
    expected_run_id: str,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
) -> StoredProtocolEvent:
    """Attach only canonical artifacts loaded from a matching persisted binding."""

    candidates = extract_mcp_app_candidates(raw_value)
    verified = await verified_mcp_app_artifacts(
        raw_value,
        expected_conversation_id=expected_conversation_id,
        expected_run_id=expected_run_id,
        session_factory=session_factory,
    )
    return {
        **event,
        "data": project_verified_mcp_apps(
            event["data"],
            verified,
            untrusted_tool_call_ids={item.tool_call_id for item in candidates},
        ),
    }


async def verified_mcp_app_artifacts(
    raw_value: Any,
    *,
    expected_conversation_id: str,
    expected_run_id: str | None = None,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve candidate references to canonical artifacts for one conversation."""

    verified: dict[str, dict[str, Any]] = {}
    for candidate in extract_mcp_app_candidates(raw_value):
        candidate_run_id = str(candidate.artifact.run_id)
        if expected_run_id is not None and candidate_run_id != expected_run_id:
            continue
        if session_factory is None:
            loaded = await load_verified_artifact(
                conversation_id=expected_conversation_id,
                run_id=candidate_run_id,
                tool_call_id=candidate.tool_call_id,
                binding_id=str(candidate.artifact.binding_id),
            )
        else:
            loaded = await load_verified_artifact(
                conversation_id=expected_conversation_id,
                run_id=candidate_run_id,
                tool_call_id=candidate.tool_call_id,
                binding_id=str(candidate.artifact.binding_id),
                session_factory=session_factory,
            )
        if loaded is not None:
            verified[candidate.tool_call_id] = loaded
    return verified


def _append_candidate(found: list[McpAppCandidate], tool_call_id: Any, artifact: Any) -> None:
    if not isinstance(tool_call_id, str) or not isinstance(artifact, Mapping):
        return
    raw_app = artifact.get("mcp_app")
    if not isinstance(raw_app, Mapping):
        return
    try:
        parsed = McpAppArtifact.model_validate(raw_app)
    except ValidationError:
        return
    found.append(McpAppCandidate(tool_call_id=tool_call_id, artifact=parsed))


def project_verified_mcp_apps(
    value: Any,
    verified: Mapping[str, dict[str, Any]],
    *,
    untrusted_tool_call_ids: set[str] | None = None,
) -> Any:
    if isinstance(value, Mapping):
        result = {
            str(key): project_verified_mcp_apps(
                item,
                verified,
                untrusted_tool_call_ids=untrusted_tool_call_ids,
            )
            for key, item in value.items()
        }
        tool_call_id = value.get("tool_call_id")
        artifact = verified.get(tool_call_id) if isinstance(tool_call_id, str) else None
        if artifact is not None:
            result["artifact"] = artifact
        elif (
            isinstance(tool_call_id, str)
            and untrusted_tool_call_ids is not None
            and tool_call_id in untrusted_tool_call_ids
        ) or (isinstance(value.get("artifact"), Mapping) and "mcp_app" in value["artifact"]):
            result.pop("artifact", None)
        return result
    if isinstance(value, list):
        return [
            project_verified_mcp_apps(
                item,
                verified,
                untrusted_tool_call_ids=untrusted_tool_call_ids,
            )
            for item in value
        ]
    return value


__all__ = [
    "attach_verified_mcp_apps",
    "extract_mcp_app_candidates",
    "project_verified_mcp_apps",
    "verified_mcp_app_artifacts",
]
