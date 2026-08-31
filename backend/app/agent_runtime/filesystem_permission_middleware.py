"""Fail-closed adapter around Deep Agents filesystem tools."""

from __future__ import annotations

from functools import wraps
from typing import Any, Final

from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission, FsToolName
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from app.agent_runtime.filesystem_permissions import (
    UnsafeFilesystemPath,
    calculate_filesystem_access,
    validate_search_pattern,
)

_SAFE_DENIAL: Final = "Error: filesystem permission denied"
_PATH_ARGUMENTS: Final = {
    "ls": "path",
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "glob": "path",
    "grep": "path",
}


def _denial(tool_name: str, runtime: Any) -> ToolMessage:
    return ToolMessage(
        content=_SAFE_DENIAL,
        name=tool_name,
        tool_call_id=getattr(runtime, "tool_call_id", "filesystem-denied"),
        status="error",
    )


def _backend_and_key(backend: BackendProtocol, path: str) -> tuple[BackendProtocol, str]:
    if not isinstance(backend, CompositeBackend):
        return backend, path
    for prefix, routed in backend.sorted_routes:
        if path.startswith(prefix):
            return _backend_and_key(routed, f"/{path.removeprefix(prefix)}")
    return _backend_and_key(backend.default, path)


def _has_symlink_component(backend: BackendProtocol, path: str) -> bool:
    resolved_backend, key = _backend_and_key(backend, path)
    if not isinstance(resolved_backend, FilesystemBackend) or not resolved_backend.virtual_mode:
        return False
    current = resolved_backend.cwd
    for part in key.strip("/").split("/"):
        if not part:
            continue
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
        if not current.exists():
            break
    return False


def _backend_is_safe(backend: BackendProtocol, path: str) -> bool:
    resolved_backend, _key = _backend_and_key(backend, path)
    return not isinstance(resolved_backend, FilesystemBackend) or bool(
        getattr(resolved_backend, "_moldy_dirfd_safe", False)
    )


def _require_safe_backend(backend: BackendProtocol) -> None:
    if isinstance(backend, CompositeBackend):
        _require_safe_backend(backend.default)
        for _prefix, routed in backend.sorted_routes:
            _require_safe_backend(routed)
        return
    if isinstance(backend, FilesystemBackend) and not getattr(backend, "_moldy_dirfd_safe", False):
        raise ValueError("scoped filesystem permissions require a secure runtime backend")


def _guarded_arguments(
    tool_name: str,
    backend: BackendProtocol,
    permissions: list[FilesystemPermission],
    kwargs: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    if tool_name == "execute":
        return kwargs, False
    path_argument = _PATH_ARGUMENTS[tool_name]
    raw_path = kwargs.get(path_argument)
    if raw_path is None and tool_name in {"glob", "grep"}:
        raw_path = "/"
    if not isinstance(raw_path, str):
        return kwargs, False
    operation = "write" if tool_name in {"write_file", "edit_file"} else "read"
    try:
        access, canonical = calculate_filesystem_access(permissions, operation, raw_path)
        if tool_name == "glob":
            validate_search_pattern(kwargs.get("pattern"))
        elif tool_name == "grep":
            validate_search_pattern(kwargs.get("glob"))
    except UnsafeFilesystemPath:
        return kwargs, False
    if (
        access not in {"allow", "interrupt"}
        or not _backend_is_safe(backend, canonical)
        or _has_symlink_component(backend, canonical)
    ):
        return kwargs, False
    guarded = dict(kwargs)
    if path_argument in guarded:
        guarded[path_argument] = canonical
    return guarded, True


def _safe_result(
    result: ToolMessage | Command,
    tool_name: str,
    runtime: Any,
    backend: BackendProtocol,
    guarded: dict[str, Any],
) -> ToolMessage | Command:
    if isinstance(result, Command):
        return result
    path_argument = _PATH_ARGUMENTS.get(tool_name)
    guarded_path = guarded.get(path_argument) if path_argument else None
    if (
        result.status == "error"
        or not isinstance(guarded_path, str)
        or _has_symlink_component(backend, guarded_path)
    ):
        return _denial(tool_name, runtime)
    return result


def _guard_tool(
    tool: BaseTool,
    backend: BackendProtocol,
    permissions: list[FilesystemPermission],
) -> BaseTool:
    if not isinstance(tool, StructuredTool):
        return tool
    original = tool.func
    original_async = tool.coroutine
    if original is None or original_async is None:
        return tool

    @wraps(original)
    def guarded_sync(*args: Any, **kwargs: Any) -> ToolMessage | Command:
        runtime = kwargs.get("runtime")
        guarded, allowed = _guarded_arguments(tool.name, backend, permissions, kwargs)
        if not allowed:
            return _denial(tool.name, runtime)
        try:
            result = original(*args, **guarded)
        except (OSError, RuntimeError, ValueError):
            return _denial(tool.name, runtime)
        return _safe_result(result, tool.name, runtime, backend, guarded)

    @wraps(original_async)
    async def guarded_async(*args: Any, **kwargs: Any) -> ToolMessage | Command:
        runtime = kwargs.get("runtime")
        guarded, allowed = _guarded_arguments(tool.name, backend, permissions, kwargs)
        if not allowed:
            return _denial(tool.name, runtime)
        try:
            result = await original_async(*args, **guarded)
        except (OSError, RuntimeError, ValueError):
            return _denial(tool.name, runtime)
        return _safe_result(result, tool.name, runtime, backend, guarded)

    tool.func = guarded_sync
    tool.coroutine = guarded_async
    return tool


class MoldyFilesystemMiddleware(FilesystemMiddleware):
    """Deep Agents filesystem surface with pre-backend fail-closed guards."""

    @property
    def name(self) -> str:
        return "FilesystemMiddleware"

    def __init__(
        self,
        *,
        backend: BackendProtocol,
        tools: list[FsToolName],
        permissions: list[FilesystemPermission] | None,
    ) -> None:
        if permissions is not None:
            _require_safe_backend(backend)
        effective_permissions = None if permissions is None else list(permissions)
        upstream_permissions = None if permissions is None else []
        super().__init__(
            backend=backend,
            tools=tools,
            _permissions=upstream_permissions,
        )
        if effective_permissions is not None:
            self._permissions = effective_permissions
            self.tools = [_guard_tool(tool, backend, effective_permissions) for tool in self.tools]


__all__ = ["MoldyFilesystemMiddleware"]
