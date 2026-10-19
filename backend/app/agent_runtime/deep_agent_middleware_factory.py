"""Deep Agents middleware-list construction for Moldy's compatibility layer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from deepagents.backends import BackendProtocol
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission, FsToolName
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models import BaseChatModel

from app.agent_runtime.filesystem_permission_middleware import MoldyFilesystemMiddleware
from app.agent_runtime.offload_storage import ScopedOffloadBackend


class _FilesystemMiddlewareBuilder(Protocol):
    def __call__(
        self,
        *,
        backend: BackendProtocol,
        permissions: list[FilesystemPermission] | None,
    ) -> FilesystemMiddleware: ...


def middleware_name_impl(middleware: Any) -> str | None:
    name = getattr(middleware, "name", None)
    return name if isinstance(name, str) else None


def build_moldy_filesystem_middleware_impl(
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    filesystem_tool_names: tuple[str, ...],
    add_scoped_permissions: Callable[[list[FilesystemPermission], Any], list[FilesystemPermission]],
) -> FilesystemMiddleware:
    effective_permissions = permissions
    if permissions is not None and isinstance(backend, ScopedOffloadBackend):
        effective_permissions = add_scoped_permissions(permissions, backend.artifacts_root)
    return MoldyFilesystemMiddleware(
        backend=backend,
        tools=cast(list[FsToolName], list(filesystem_tool_names)),
        permissions=effective_permissions,
    )


def with_moldy_deepagents_compatibility_impl(
    middleware: list[Any] | tuple[Any, ...] | None,
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    middleware_name: Callable[[Any], str | None],
    build_filesystem_middleware: _FilesystemMiddlewareBuilder,
    filesystem_middleware_name: str,
    todo_list_middleware_name: str,
    include_todo: bool = True,
) -> list[Any]:
    excluded_names = {filesystem_middleware_name, todo_list_middleware_name}
    retained = [item for item in (middleware or ()) if middleware_name(item) not in excluded_names]
    filesystem = build_filesystem_middleware(
        backend=cast(BackendProtocol, backend),
        permissions=permissions,
    )
    compatibility_middleware: list[Any] = [filesystem]
    if include_todo:
        compatibility_middleware.append(TodoListMiddleware())
    return [*compatibility_middleware, *retained]


def with_actor_summarization_impl(
    middleware: list[Any],
    *,
    model: BaseChatModel,
    backend: Any,
    middleware_name: Callable[[Any], str | None],
    create_summarization_middleware: Callable[[BaseChatModel, Any], Any],
    summarization_middleware_name: str,
) -> list[Any]:
    if not isinstance(backend, ScopedOffloadBackend):
        return middleware
    retained = [
        item for item in middleware if middleware_name(item) != summarization_middleware_name
    ]
    return [*retained, create_summarization_middleware(model, backend)]
