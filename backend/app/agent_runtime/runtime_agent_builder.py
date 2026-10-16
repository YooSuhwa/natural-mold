"""Deep Agents graph construction with Moldy's filesystem/todo compatibility.

Collaborators that tests patch on ``runtime_component_builder`` are resolved
through the live facade at call time.
"""

from __future__ import annotations

from functools import partial
from typing import Any, assert_never

from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agent_runtime.deep_agent_factory import (
    DeepAgentFactoryBindings as _DeepAgentFactoryBindings,
)
from app.agent_runtime.deep_agent_factory import (
    build_agent_impl as _build_agent_impl,
)
from app.agent_runtime.deep_agent_factory import (
    build_moldy_filesystem_middleware_impl as _build_moldy_filesystem_middleware_impl,
)
from app.agent_runtime.deep_agent_factory import (
    middleware_name_impl as _middleware_name_impl,
)
from app.agent_runtime.deep_agent_factory import (
    with_moldy_deepagents_compatibility_impl as _with_moldy_deepagents_compatibility_impl,
)
from app.agent_runtime.filesystem_permissions import add_scoped_offload_permissions
from app.agent_runtime.runtime_facade_bindings import runtime_attr
from app.agent_runtime.runtime_policy import ResolvedRuntimePolicy
from app.agent_runtime.runtime_policy_capabilities import STORED_RESERVED_TOOL_NAMES
from app.agent_runtime.runtime_policy_parent_permissions import (
    canonicalize_stored_parent_permissions,
)
from app.agent_runtime.runtime_subagent_normalization import _SUMMARIZATION_MIDDLEWARE_NAME
from app.agent_runtime.runtime_summarization_policy import (
    create_stored_summarization_middleware,
)

# Deep Agents 0.7 makes ``delete`` part of its default filesystem surface and
# stops installing TodoListMiddleware. Moldy intentionally preserves its
# pre-0.7 non-deleting filesystem capability set and todo-state stream
# contract here, at the single build boundary used by chat, Assistant, and
# Skill Builder.
_MOLDY_FILESYSTEM_TOOL_NAMES = (
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
)
_INSPECT_FILESYSTEM_TOOL_NAMES = ("ls", "read_file", "glob", "grep")
_ARTIFACT_WRITE_FILESYSTEM_TOOL_NAMES = (
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
)
_FILESYSTEM_MIDDLEWARE_NAME = "FilesystemMiddleware"
_TODO_LIST_MIDDLEWARE_NAME = "TodoListMiddleware"


def _middleware_name(middleware: Any) -> str | None:
    """Return a middleware's public name without assuming a concrete type."""

    return _middleware_name_impl(middleware)


def _build_moldy_filesystem_middleware(
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
) -> FilesystemMiddleware:
    """Build Moldy's deliberately non-deleting Deep Agents filesystem layer."""

    return _build_moldy_filesystem_middleware_impl(
        backend=backend,
        permissions=permissions,
        filesystem_tool_names=_MOLDY_FILESYSTEM_TOOL_NAMES,
        add_scoped_permissions=add_scoped_offload_permissions,
    )


def _with_moldy_deepagents_compatibility(
    middleware: list[Any] | tuple[Any, ...] | None,
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
) -> list[Any]:
    """Replace Deep Agents 0.7 default FS/todo behavior with Moldy's contract.

    The two compatibility middleware entries are intentionally recreated rather
    than reusing caller instances: the filesystem instance must share the same
    backend and effective permission list as the graph it configures.  All
    unrelated middleware retain their caller-provided relative order.
    """

    return _with_moldy_deepagents_compatibility_impl(
        middleware,
        backend=backend,
        permissions=permissions,
        middleware_name=runtime_attr("_middleware_name"),
        build_filesystem_middleware=runtime_attr("_build_moldy_filesystem_middleware"),
        filesystem_middleware_name=_FILESYSTEM_MIDDLEWARE_NAME,
        todo_list_middleware_name=_TODO_LIST_MIDDLEWARE_NAME,
    )


def _stored_filesystem_tool_names(
    runtime_policy: ResolvedRuntimePolicy,
) -> tuple[str, ...]:
    match runtime_policy.effective.filesystem.mode:
        case "inspect":
            return _INSPECT_FILESYSTEM_TOOL_NAMES
        case "artifact_write":
            return _ARTIFACT_WRITE_FILESYSTEM_TOOL_NAMES
        case unreachable:
            assert_never(unreachable)


def _with_stored_policy_compatibility(
    middleware: list[Any] | tuple[Any, ...] | None,
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    filesystem_tool_names: tuple[str, ...],
    todo_enabled: bool,
) -> list[Any]:
    def build_filesystem_middleware(
        *,
        backend: Any,
        permissions: list[FilesystemPermission] | None,
    ) -> FilesystemMiddleware:
        return _build_moldy_filesystem_middleware_impl(
            backend=backend,
            permissions=permissions,
            filesystem_tool_names=filesystem_tool_names,
            add_scoped_permissions=add_scoped_offload_permissions,
        )

    return _with_moldy_deepagents_compatibility_impl(
        middleware,
        backend=backend,
        permissions=permissions,
        middleware_name=runtime_attr("_middleware_name"),
        build_filesystem_middleware=build_filesystem_middleware,
        filesystem_middleware_name=_FILESYSTEM_MIDDLEWARE_NAME,
        todo_list_middleware_name=_TODO_LIST_MIDDLEWARE_NAME,
        include_todo=todo_enabled,
    )


def _with_stored_policy_summarization(
    middleware: list[Any],
    *,
    model: BaseChatModel,
    backend: Any,
    runtime_policy: ResolvedRuntimePolicy,
) -> list[Any]:
    """Replace any caller summarizer with the snapshotted stored policy."""

    middleware_name = runtime_attr("_middleware_name")
    retained = [
        item for item in middleware if middleware_name(item) != _SUMMARIZATION_MIDDLEWARE_NAME
    ]
    return [
        *retained,
        create_stored_summarization_middleware(runtime_policy, model, backend),
    ]


def _with_stored_parent_policy(
    middleware: list[Any] | tuple[Any, ...] | None,
    *,
    backend: Any,
    permissions: list[FilesystemPermission] | None,
    filesystem_tool_names: tuple[str, ...],
    todo_enabled: bool,
    model: BaseChatModel,
    runtime_policy: ResolvedRuntimePolicy,
) -> list[Any]:
    """Build the top-level stored-policy compatibility stack."""

    compatible = runtime_attr("_with_stored_policy_compatibility")(
        middleware,
        backend=backend,
        permissions=permissions,
        filesystem_tool_names=filesystem_tool_names,
        todo_enabled=todo_enabled,
    )
    return runtime_attr("_with_stored_policy_summarization")(
        compatible,
        model=model,
        backend=backend,
        runtime_policy=runtime_policy,
    )


def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    *,
    middleware: list | None = None,
    interrupt_on: dict[str, Any] | bool | None = None,
    checkpointer: Any | None = None,
    store: Any | None = None,
    backend: Any | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    name: str | None = None,
    subagents: list[dict[str, Any]] | None = None,
    runtime_policy: ResolvedRuntimePolicy | None = None,
) -> Any:
    """Build a moldy agent. Returns CompiledStateGraph."""
    effective_permissions = permissions
    effective_tools = tools
    bindings = _DeepAgentFactoryBindings(
        create_deep_agent=runtime_attr("create_deep_agent"),
        with_compatibility=runtime_attr("_with_moldy_deepagents_compatibility"),
        normalize_subagents=runtime_attr("_normalize_declarative_subagents"),
    )
    if runtime_policy is not None and runtime_policy.source == "stored":
        effective_tools = [tool for tool in tools if tool.name not in STORED_RESERVED_TOOL_NAMES]
        effective_permissions = canonicalize_stored_parent_permissions(
            permissions,
            mode=runtime_policy.effective.filesystem.mode,
        )
        filesystem_tool_names = runtime_attr("_stored_filesystem_tool_names")(runtime_policy)
        todo_enabled = runtime_policy.effective.todo.enabled
        bindings = _DeepAgentFactoryBindings(
            create_deep_agent=runtime_attr("create_deep_agent"),
            with_compatibility=partial(
                runtime_attr("_with_stored_parent_policy"),
                filesystem_tool_names=filesystem_tool_names,
                todo_enabled=todo_enabled,
                model=model,
                runtime_policy=runtime_policy,
            ),
            normalize_subagents=partial(
                runtime_attr("_normalize_stored_policy_subagents"),
                filesystem_tool_names=filesystem_tool_names,
                todo_enabled=todo_enabled,
            ),
        )
    return _build_agent_impl(
        model=model,
        tools=effective_tools,
        system_prompt=system_prompt,
        middleware=middleware,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        store=store,
        backend=backend,
        skills=skills,
        memory=memory,
        permissions=effective_permissions,
        name=name,
        subagents=subagents,
        bindings=bindings,
    )
