"""String projection for trusted Deep Agents offload references."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from app.agent_runtime.offload_storage_types import (
    OffloadKind,
    OffloadProjection,
    logical_offload_id,
)

INTERNAL_REFERENCE_REDACTED: Final = "internal_reference_redacted"

_SCOPE_DIGEST = r"[0-9a-f]{32}"
_SESSION = r"session_[0-9a-f]{32}\.md"
_MEDIA = r"media/[0-9a-f]{16}\.[a-z0-9]{1,10}"
_SAFE_SPILL_LEAF = r"[^/\x00\r\n]+"
_REFERENCE_BOUNDARY = r"(?<![A-Za-z0-9_./-])"
_REFERENCE_END = r"(?![A-Za-z0-9_./-])"
_VIRTUAL_HISTORY_RE = re.compile(
    rf"{_REFERENCE_BOUNDARY}(?P<path>/\.moldy-offload/{_SCOPE_DIGEST}/"
    rf"{_SCOPE_DIGEST}/{_SCOPE_DIGEST}/conversation_history/(?:{_SESSION}|{_MEDIA}))"
    rf"{_REFERENCE_END}"
)
_LEGACY_HISTORY_RE = re.compile(
    rf"{_REFERENCE_BOUNDARY}(?P<path>/conversation_history/(?:{_SESSION}|{_MEDIA}))"
    rf"{_REFERENCE_END}"
)
_SCOPED_SPILL_FULL_RE = re.compile(
    rf"/\.moldy-offload/{_SCOPE_DIGEST}/{_SCOPE_DIGEST}/{_SCOPE_DIGEST}/"
    rf"large_tool_results/{_SAFE_SPILL_LEAF}"
)
_LEGACY_SPILL_FULL_RE = re.compile(rf"/large_tool_results/{_SAFE_SPILL_LEAF}")
_TOOL_POINTER_RE = re.compile(
    r"Tool result too large, the result of this tool call (?P<message_id>[^\r\n]+?) "
    r"was saved in the filesystem at this path: "
    rf"(?P<path>(?:/\.moldy-offload/{_SCOPE_DIGEST}/{_SCOPE_DIGEST}/{_SCOPE_DIGEST}"
    rf"/large_tool_results|/large_tool_results)/(?P<leaf>{_SAFE_SPILL_LEAF}))\n\n"
)
_INTERNAL_MARKER_RE = re.compile(
    r"\.moldy-internal/offload/|/\.moldy-offload/|"
    r"(?<![A-Za-z0-9_./-])/(?:conversation_history|large_tool_results)/"
)


def contains_internal_offload_marker(value: str) -> bool:
    """Return whether a scalar claims an internal offload namespace."""

    return _INTERNAL_MARKER_RE.search(value) is not None


def _projection(kind: OffloadKind, path: str) -> OffloadProjection:
    return OffloadProjection(kind=kind, logical_id=logical_offload_id(kind, path))


def _physical_patterns(
    roots: tuple[Path, ...],
) -> tuple[tuple[re.Pattern[str], OffloadKind], ...]:
    patterns: list[tuple[re.Pattern[str], OffloadKind]] = []
    history_suffix = rf"{_SCOPE_DIGEST}/{_SCOPE_DIGEST}/{_SCOPE_DIGEST}(?:/{_SCOPE_DIGEST})?"
    spill_suffix = (
        rf"{_SCOPE_DIGEST}/{_SCOPE_DIGEST}/{_SCOPE_DIGEST}/"
        rf"{_SCOPE_DIGEST}/{_SCOPE_DIGEST}"
    )
    for root in roots:
        internal = re.escape(str(root.resolve() / ".moldy-internal" / "offload"))
        patterns.extend(
            (
                (
                    re.compile(
                        rf"{_REFERENCE_BOUNDARY}(?P<path>{internal}/history/"
                        rf"{history_suffix}){_REFERENCE_END}"
                    ),
                    OffloadKind.HISTORY,
                ),
                (
                    re.compile(
                        rf"{_REFERENCE_BOUNDARY}(?P<path>{internal}/spill/"
                        rf"{spill_suffix}){_REFERENCE_END}"
                    ),
                    OffloadKind.SPILL,
                ),
            )
        )
    return tuple(patterns)


def _physical_projection(path: str, roots: tuple[Path, ...]) -> OffloadProjection | None:
    for pattern, kind in _physical_patterns(roots):
        match = pattern.fullmatch(path)
        if match is not None:
            return _projection(kind, path)
    return None


def project_structural_reference(path: str, roots: tuple[Path, ...]) -> OffloadProjection | None:
    """Parse an exact internal path emitted by the configured storage boundary."""

    if _VIRTUAL_HISTORY_RE.fullmatch(path) or _LEGACY_HISTORY_RE.fullmatch(path):
        return _projection(OffloadKind.HISTORY, path)
    if _SCOPED_SPILL_FULL_RE.fullmatch(path) or _LEGACY_SPILL_FULL_RE.fullmatch(path):
        return _projection(OffloadKind.SPILL, path)
    return _physical_projection(path, roots)


def _replace_matches(value: str, pattern: re.Pattern[str], kind: OffloadKind) -> str:
    return pattern.sub(lambda match: logical_offload_id(kind, match.group("path")), value)


def _replace_physical(value: str, roots: tuple[Path, ...]) -> str:
    projected = value
    for pattern, kind in _physical_patterns(roots):
        projected = _replace_matches(projected, pattern, kind)
    return projected


def _sanitize_tool_call_id(tool_call_id: str) -> str:
    return tool_call_id.replace(".", "_").replace("/", "_").replace("\\", "_")


def _replace_tool_pointers(value: str, tool_call_id: str | None) -> str:
    def replace(match: re.Match[str]) -> str:
        valid_pointer = (
            tool_call_id is not None
            and match.group("message_id") == tool_call_id
            and match.group("leaf") == _sanitize_tool_call_id(tool_call_id)
        )
        if not valid_pointer:
            return match.group(0)
        path = match.group("path")
        return match.group(0).replace(path, logical_offload_id(OffloadKind.SPILL, path), 1)

    return _TOOL_POINTER_RE.sub(replace, value)


def project_offload_string(
    value: str, roots: tuple[Path, ...], *, tool_call_id: str | None = None
) -> str:
    """Replace only structurally known references and fail closed on leftovers."""

    projected = _replace_tool_pointers(value, tool_call_id)
    projected = _replace_physical(projected, roots)
    projected = _replace_matches(projected, _VIRTUAL_HISTORY_RE, OffloadKind.HISTORY)
    projected = _replace_matches(projected, _LEGACY_HISTORY_RE, OffloadKind.HISTORY)
    if contains_internal_offload_marker(projected):
        return INTERNAL_REFERENCE_REDACTED
    return projected


def freeze_trusted_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
    """Snapshot injected roots so one projection cannot observe later mutation."""

    return tuple(dict.fromkeys(root.resolve() for root in roots))
