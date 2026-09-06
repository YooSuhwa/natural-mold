"""Protocol-only projection for internal Deep Agents offload references.

Raw checkpoint and graph state retain storage paths for resume. Browser-facing
boundaries call :func:`project_offload_egress_data` on a copied value.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app.agent_runtime.offload_protocol_string_projection import (
    INTERNAL_REFERENCE_REDACTED,
    contains_internal_offload_marker,
    freeze_trusted_roots,
    project_offload_string,
    project_structural_reference,
)
from app.agent_runtime.offload_storage_types import OffloadKind, OffloadProjection

_RESERVED_OFFLOAD_KEYS: Final = frozenset({"history_id", "spill_id"})
_INTERNAL_REDACTION_KEY: Final = INTERNAL_REFERENCE_REDACTED
_LOGICAL_ID_RE: Final = re.compile(r"(?P<kind>history|spill)_[0-9a-f]{24}\Z")
_COMPACTION_EVENT_NAME: Final = "moldy.compaction"


@dataclass(frozen=True, slots=True)
class _ProjectionContext:
    roots: tuple[Path, ...]
    tool_call_id: str | None = None


def _frozen_roots(roots: Collection[Path] | None) -> tuple[Path, ...]:
    if roots is None:
        from app.agent_runtime import runtime_config

        return freeze_trusted_roots((runtime_config.runtime_data_dir(),))
    return freeze_trusted_roots(roots)


def project_offload_reference(
    path: str, *, roots: Collection[Path] | None = None
) -> OffloadProjection | None:
    """Project one exact trusted storage reference without exposing its path."""

    return project_structural_reference(path, _frozen_roots(roots))


def _existing_logical_id(data: Mapping[Any, Any], *, path_key: str) -> tuple[str, str] | None:
    present = tuple(key for key in _RESERVED_OFFLOAD_KEYS if key in data)
    if len(present) != 1:
        return None
    key = present[0]
    value = data.get(key)
    if not isinstance(value, str):
        return None
    match = _LOGICAL_ID_RE.fullmatch(value)
    if match is None or key != f"{match.group('kind')}_id":
        return None
    if path_key == "file_path" and key != "history_id":
        return None
    return key, value


def _project_structural_mapping(
    data: Mapping[Any, Any], *, path_key: str, context: _ProjectionContext
) -> dict[Any, Any]:
    raw_path = data.get(path_key)
    projection = (
        project_structural_reference(raw_path, context.roots)
        if isinstance(raw_path, str) and raw_path
        else None
    )
    if (
        path_key == "file_path"
        and projection is not None
        and projection.kind is not OffloadKind.HISTORY
    ):
        projection = None
    # ``offload_path`` is always authoritative.  A projected summary can be
    # projected again while it is replayed, but an offload payload without its
    # path must not turn a caller-supplied ``spill_id`` into trusted state.
    existing = (
        _existing_logical_id(data, path_key=path_key)
        if path_key == "file_path" and raw_path is None
        else None
    )
    projected: dict[Any, Any] = {}
    for raw_key, item in data.items():
        if raw_key == path_key or raw_key in _RESERVED_OFFLOAD_KEYS:
            continue
        key = _project_value(raw_key, context=context)
        if raw_key == "_summarization_event" and isinstance(item, Mapping):
            projected[key] = _project_structural_mapping(
                item, path_key="file_path", context=context
            )
        else:
            projected[key] = _project_value(item, context=context)
    if projection is not None:
        projected[f"{projection.kind.value}_id"] = projection.logical_id
    elif existing is not None:
        projected[existing[0]] = existing[1]
    else:
        projected[_INTERNAL_REDACTION_KEY] = True
    return projected


def _project_mapping(data: Mapping[Any, Any], *, context: _ProjectionContext) -> dict[Any, Any]:
    raw_offload_path = data.get("offload_path")
    is_internal_offload_path = isinstance(raw_offload_path, str) and (
        project_structural_reference(raw_offload_path, context.roots) is not None
        or contains_internal_offload_marker(raw_offload_path)
    )
    if is_internal_offload_path:
        return _project_structural_mapping(data, path_key="offload_path", context=context)
    if data.get("name") == _COMPACTION_EVENT_NAME and isinstance(data.get("payload"), Mapping):
        return _project_compaction_event(data, context=context)

    own_tool_call_id = data.get("tool_call_id")
    content_tool_call_id = (
        own_tool_call_id if isinstance(own_tool_call_id, str) and own_tool_call_id else None
    )
    projected: dict[Any, Any] = {}
    for raw_key, item in data.items():
        key = _project_value(raw_key, context=context)
        if raw_key == "_summarization_event" and isinstance(item, Mapping):
            projected[key] = _project_structural_mapping(
                item, path_key="file_path", context=context
            )
        elif raw_key in {"content", "content_blocks"}:
            projected[key] = _project_tool_content(
                item, roots=context.roots, tool_call_id=content_tool_call_id
            )
        else:
            # A nested arbitrary mapping is not a tool message merely because
            # an ancestor happened to carry ``tool_call_id``.
            projected[key] = _project_value(item, context=_ProjectionContext(context.roots))
    return projected


def _project_compaction_event(
    data: Mapping[Any, Any], *, context: _ProjectionContext
) -> dict[Any, Any]:
    """Retain only the validated history ID in an already-emitted done marker."""

    payload = data["payload"]
    if not isinstance(payload, Mapping):
        return {key: _project_value(value, context=context) for key, value in data.items()}
    projected: dict[Any, Any] = {}
    for raw_key, item in data.items():
        key = _project_value(raw_key, context=context)
        if raw_key == "payload":
            projected[key] = _project_compaction_payload(item, context=context)
        elif raw_key not in _RESERVED_OFFLOAD_KEYS:
            projected[key] = _project_value(item, context=_ProjectionContext(context.roots))
    return projected


def _project_compaction_payload(
    payload: Mapping[Any, Any], *, context: _ProjectionContext
) -> dict[Any, Any]:
    projected = {
        _project_value(key, context=context): _project_value(
            value, context=_ProjectionContext(context.roots)
        )
        for key, value in payload.items()
        if key not in _RESERVED_OFFLOAD_KEYS
    }
    history_id = _existing_logical_id(payload, path_key="file_path")
    if (
        payload.get("state") == "done"
        and "file_path" not in payload
        and "offload_path" not in payload
        and history_id is not None
    ):
        projected[history_id[0]] = history_id[1]
    return projected


def _project_tool_content(data: Any, *, roots: tuple[Path, ...], tool_call_id: str | None) -> Any:
    """Project a ToolMessage content field without lending its ID to metadata."""

    if isinstance(data, str):
        return project_offload_string(data, roots, tool_call_id=tool_call_id)
    if isinstance(data, list):
        return [
            _project_tool_content_block(item, roots=roots, tool_call_id=tool_call_id)
            for item in data
        ]
    if isinstance(data, tuple):
        return tuple(
            _project_tool_content_block(item, roots=roots, tool_call_id=tool_call_id)
            for item in data
        )
    if isinstance(data, Mapping):
        return _project_tool_content_block(data, roots=roots, tool_call_id=tool_call_id)
    return _project_value(data, context=_ProjectionContext(roots))


def _project_tool_content_block(
    data: Any, *, roots: tuple[Path, ...], tool_call_id: str | None
) -> Any:
    """Pass the ToolMessage identity only to actual text/content block values."""

    if not isinstance(data, Mapping):
        return _project_tool_content(data, roots=roots, tool_call_id=tool_call_id)
    projected: dict[Any, Any] = {}
    for raw_key, item in data.items():
        key = _project_value(raw_key, context=_ProjectionContext(roots))
        if raw_key in {"text", "content"}:
            projected[key] = _project_tool_content(item, roots=roots, tool_call_id=tool_call_id)
        else:
            projected[key] = _project_value(item, context=_ProjectionContext(roots))
    return projected


def _project_value(data: Any, *, context: _ProjectionContext) -> Any:
    if isinstance(data, Mapping):
        return _project_mapping(data, context=context)
    if isinstance(data, list):
        return [_project_value(value, context=context) for value in data]
    if isinstance(data, tuple):
        return tuple(_project_value(value, context=context) for value in data)
    if isinstance(data, str):
        return project_offload_string(data, context.roots, tool_call_id=context.tool_call_id)
    return data


def project_offload_egress_data(data: Any, *, roots: Collection[Path] | None = None) -> Any:
    """Copy protocol data and replace trusted internal paths with logical IDs."""

    return _project_value(data, context=_ProjectionContext(_frozen_roots(roots)))
